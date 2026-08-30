"""Say whether a GitHub App installation is suspended, and stop retrying if so.

Read only. GETs against the App's own installation records with the App JWT,
plus one optional probe with an installation access token. Nothing is minted,
unsuspended or changed. There is an endpoint that unsuspends an installation
and it is a write, so this script does not call it; it prints the request you
have to make of an organization owner instead.

An owner can suspend an installation rather than removing it. The record
survives, so the App still lists it and a stored id still resolves, but tokens
minted for it are refused and webhook delivery stops. The only clean signal is
the suspended_at field on the installation record, which is readable with the
App's JWT and not with a token minted from it.

Environment:

    GITHUB_APP_JWT              the JWT your own signing code produced
    GITHUB_INSTALLATION_TOKEN   optional, used only to corroborate
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_installation_suspension")

API = "https://api.github.com"
UA = "github-installation-suspension/1.0"

# States that no amount of waiting will clear. Reporting this is the point of
# the script: a suspended installation fails identically forever, so a backoff
# loop around it is a way of spending quota on a decision somebody already made.
TERMINAL = ("suspended", "not-listed")


def suspended_at(inst):
    """The suspension timestamp on an installation record, or None. Pure.

    Tolerant on purpose. Depending on which layer deserialised the JSON the
    absent case arrives as a missing key, as None, as an empty string or as the
    four characters n-u-l-l, and treating any of those as a timestamp would
    report a healthy installation as suspended.
    """
    if not isinstance(inst, dict):
        return None
    raw = inst.get("suspended_at")
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in ("null", "none"):
        return None
    return text


def is_suspended(inst):
    """Whether an installation record carries a suspension. Pure."""
    return suspended_at(inst) is not None


def suspended_by(inst):
    """Who suspended it, where the record says. Pure.

    suspended_by is a user object when it is present at all, and it can be
    absent on a record that is genuinely suspended, so an unknown actor is
    never taken as evidence that nothing happened.
    """
    if not isinstance(inst, dict):
        return None
    who = inst.get("suspended_by")
    if isinstance(who, dict):
        login = who.get("login")
        return str(login) if login else None
    if isinstance(who, str) and who.strip():
        return who.strip()
    return None


def parsed_time(text):
    """An ISO 8601 timestamp as an aware datetime, or None. Pure."""
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def days_since(text, now):
    """Whole days between a timestamp and now, or None. Pure."""
    when = parsed_time(text)
    if when is None or now is None:
        return None
    return (now - when).days


def account_of(inst):
    """The login of the account an installation sits on. Pure."""
    if not isinstance(inst, dict):
        return "an unnamed account"
    account = inst.get("account")
    if isinstance(account, dict) and account.get("login"):
        return str(account["login"])
    return "an unnamed account"


def find(installations, installation_id):
    """The record for one installation id, or None. Pure.

    The id is compared as text because it reaches this script from a config
    file, an environment variable or a command line, and 41234567 and the
    string "41234567" are the same installation.
    """
    if installation_id is None:
        return None
    wanted = str(installation_id).strip()
    for inst in installations or []:
        if isinstance(inst, dict) and str(inst.get("id", "")).strip() == wanted:
            return inst
    return None


def summarize(installations):
    """Counts across every installation the App can see. Pure."""
    rows = [i for i in (installations or []) if isinstance(i, dict)]
    suspended = [i for i in rows if is_suspended(i)]
    return {"total": len(rows), "suspended": len(suspended),
            "active": len(rows) - len(suspended),
            "suspended_ids": [i.get("id") for i in suspended]}


def verdict(target, probe_status=None, now=None):
    """Turn one installation record and an optional probe into a finding. Pure.

    target is the record for the installation being asked about, or None when
    the id is not in the list at all. probe_status is what an installation
    access token got from GET /installation/repositories, where one was
    available; it corroborates and never decides, because a 403 on its own is
    the least specific thing the GitHub API says.
    """
    if target is None:
        return ("not-listed",
                "this installation id is not among the ones the App can see. "
                "Suspension keeps the record, so an absent id means the App "
                "was removed and possibly reinstalled under a new id, which "
                "is a different repair.")
    ident = "installation %s on %s" % (target.get("id", "?"), account_of(target))
    when = suspended_at(target)
    if when is not None:
        age = days_since(when, now)
        who = suspended_by(target)
        return ("suspended",
                "%s was suspended at %s%s%s. Every token minted for it is "
                "refused and webhook delivery has stopped. Retrying cannot "
                "clear this." % (ident, when,
                                 " by %s" % who if who else "",
                                 ", %d day(s) ago" % age if age is not None else ""))
    if probe_status in (401, 403):
        return ("active-but-refused",
                "%s is listed and not suspended, yet an installation token "
                "got %d. The refusal is about a permission, a route or the "
                "token itself rather than about suspension."
                % (ident, probe_status))
    return ("active", "%s is listed and not suspended." % ident)


def retryable(state):
    """Whether a caller should ever try this installation again. Pure."""
    return state not in TERMINAL


def repair(state, target):
    """The sentence a reader has to act on. Pure."""
    if state == "suspended":
        return ("an organization owner unsuspends it from the %s account's "
                "Installed GitHub Apps page. Retrying cannot help: stop the "
                "queue for this installation and alert once."
                % account_of(target))
    if state == "not-listed":
        return ("resolve the installation id at runtime from the org's own "
                "installation record, or from the installation.id field on an "
                "incoming webhook, rather than storing it.")
    if state == "active-but-refused":
        return ("read the accepted-permissions header on the failing response "
                "and diff it against the permissions this installation "
                "granted. Suspension is not the cause here.")
    return "nothing. This installation is usable."


def get(session, path):
    """One GET. Returns (status, json-or-None)."""
    url = API + path if path.startswith("/") else path
    r = session.get(url, timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body


def list_installations(session, pages=10):
    """Every installation this App can see, paginated. Read only."""
    out = []
    for page in range(1, pages + 1):
        status, body = get(session, "/app/installations?per_page=100&page=%d" % page)
        if status != 200 or not isinstance(body, list):
            if page == 1:
                log.error("GET /app/installations returned %d; the JWT is the "
                          "credential this endpoint wants", status)
            break
        out.extend(body)
        if len(body) < 100:
            break
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--installation-id",
                    default=os.environ.get("GITHUB_INSTALLATION_ID"),
                    help="the installation to ask about; omit to report on all")
    args = ap.parse_args()

    jwt = os.environ.get("GITHUB_APP_JWT")
    if not jwt:
        log.error("set GITHUB_APP_JWT to the JWT your own signing code "
                  "produced. suspended_at lives on the installation record, "
                  "and installation records are read with the App's JWT")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + jwt,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    installations = list_installations(session)
    stats = summarize(installations)
    log.info("%d installation(s) visible to this App, %d suspended",
             stats["total"], stats["suspended"])

    probe_status = None
    token = os.environ.get("GITHUB_INSTALLATION_TOKEN")
    if token:
        probe = requests.Session()
        probe.headers.update({
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": UA,
        })
        probe_status, _ = get(probe, "/installation/repositories?per_page=1")
        log.info("installation token: GET /installation/repositories returned %d",
                 probe_status)

    now = datetime.now(timezone.utc)
    findings = []
    if args.installation_id:
        target = find(installations, args.installation_id)
        state, detail = verdict(target, probe_status, now)
        findings.append({"installation_id": args.installation_id, "state": state,
                         "detail": detail, "retryable": retryable(state)})
        log.info("%s: %s", state, detail)
        log.info("repair: %s", repair(state, target))
    else:
        for inst in installations:
            state, detail = verdict(inst, None, now)
            findings.append({"installation_id": inst.get("id"), "state": state,
                             "detail": detail, "retryable": retryable(state)})
            if state != "active":
                log.info("%s: %s", state, detail)
                log.info("repair: %s", repair(state, inst))
        if stats["suspended"] == 0:
            log.info("active: no installation of this App is suspended")

    print(json.dumps({"visible": stats["total"], "suspended": stats["suspended"],
                      "suspended_ids": stats["suspended_ids"],
                      "probe_status": probe_status,
                      "findings": findings}, indent=2, default=str))
    return 1 if any(not f["retryable"] for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
