"""Find configured GitHub App installation ids that no longer mean what they did.

Read only. One paginated GET over the App's own installations with the App JWT,
and one optional GET per configured account. Nothing is minted or changed. The
endpoint that mints an installation access token is a write, so this script does
not call it and never reproduces the 404 that usually starts the investigation.

Installation ids are not stable. Uninstalling and reinstalling an App creates a
new installation with a new id, so an id copied out of a URL once either stops
resolving or, if it was transposed or reused, resolves against an account that
is not the one your configuration believes.

Environment:

    GITHUB_APP_JWT           the JWT your own signing code produced
    GITHUB_INSTALLATION_MAP  account=id pairs, comma separated, or a JSON object
    GITHUB_MAP_RECORDED_AT   optional ISO date the map was last written
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_installation_id_drift")

API = "https://api.github.com"
UA = "github-installation-id-drift/1.0"

# The one finding that never announces itself. A stale id 404s on the next call;
# a crossed id succeeds forever against the wrong organization, so it is ordered
# first in every report this script prints.
SILENT = ("crossed",)


def parse_map(text):
    """account=id pairs, or a JSON object, into a plain dict. Pure.

    Accepts both because this value lives in an environment variable in some
    deployments and in a config file in others, and a checker that only reads
    one of the two shapes gets skipped in half of them.
    """
    raw = str(text or "").strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        try:
            loaded = json.loads(raw)
        except ValueError:
            return {}
        return {str(k).strip().lower(): str(v).strip()
                for k, v in loaded.items() if str(k).strip()}
    out = {}
    for chunk in raw.replace(";", ",").split(","):
        if "=" not in chunk:
            continue
        account, _, ident = chunk.partition("=")
        account, ident = account.strip().lower(), ident.strip()
        if account and ident:
            out[account] = ident
    return out


def account_of(inst):
    """The login of the account an installation sits on. Pure."""
    if not isinstance(inst, dict):
        return None
    account = inst.get("account")
    if isinstance(account, dict) and account.get("login"):
        return str(account["login"])
    return None


def stable_key(inst):
    """The value worth keying stored state on. Pure.

    The login rather than the installation id, lowercased so the same account
    written two ways is one key. This is the whole recommendation of the note,
    expressed as a function so it can be tested rather than only asserted.
    """
    login = account_of(inst)
    return login.lower() if login else None


def index_by_id(installations):
    """Installations by their id, as text. Pure."""
    out = {}
    for inst in installations or []:
        if isinstance(inst, dict) and inst.get("id") is not None:
            out[str(inst["id"]).strip()] = inst
    return out


def index_by_account(installations):
    """Installations by lowercased account login. Pure."""
    out = {}
    for inst in installations or []:
        key = stable_key(inst)
        if key:
            out[key] = inst
    return out


def current_id_for(account, by_account):
    """The id this account's installation has right now, or None. Pure."""
    inst = (by_account or {}).get(str(account or "").strip().lower())
    return str(inst["id"]) if isinstance(inst, dict) and inst.get("id") is not None else None


def parse_moment(text):
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


def reinstalled_since(inst, recorded_at):
    """Whether this installation was created after the map was written. Pure.

    None when either date is unreadable, which is a third answer rather than a
    False: not knowing is not the same as knowing nothing happened.
    """
    created = parse_moment((inst or {}).get("created_at") if isinstance(inst, dict) else None)
    recorded = parse_moment(recorded_at)
    if created is None or recorded is None:
        return None
    return created > recorded


def drift(account, configured_id, by_id, by_account, recorded_at=None):
    """Compare one configured pair against reality. Pure."""
    account = str(account or "").strip()
    wanted = str(configured_id or "").strip()
    listed = (by_id or {}).get(wanted)
    current = current_id_for(account, by_account)

    if listed is not None:
        owner = account_of(listed) or ""
        if owner.lower() != account.lower():
            return ("crossed",
                    "%s is configured as %s, which exists and belongs to %s. "
                    "Nothing about this fails: it works against the wrong "
                    "account." % (account, wanted, owner or "another account"))
        fresh = reinstalled_since(listed, recorded_at)
        if fresh:
            return ("current-but-reinstalled",
                    "%s still resolves to %s, and that installation was created "
                    "after the map was written, so the App was removed and "
                    "re-added at some point." % (account, wanted))
        return ("current", "%s resolves to %s." % (account, wanted))

    if current is not None:
        created = (by_account.get(account.lower()) or {}).get("created_at")
        return ("stale",
                "%s is configured as %s, which this App no longer has. The "
                "current installation for %s is %s%s."
                % (account, wanted, account, current,
                   ", created %s" % created if created else ""))
    return ("gone",
            "%s is configured as %s and this App has no installation on that "
            "account at all. It was uninstalled and not put back."
            % (account, wanted))


def unmapped(by_account, configured):
    """Accounts the App is installed on that the configuration omits. Pure."""
    known = {str(k).strip().lower() for k in (configured or {})}
    return sorted(k for k in (by_account or {}) if k not in known)


def summarize(findings):
    """Counts by state, with the silent finding pulled out. Pure."""
    counts = {}
    for f in findings or []:
        counts[f["state"]] = counts.get(f["state"], 0) + 1
    return {"total": len(findings or []), "by_state": counts,
            "silent": sum(counts.get(s, 0) for s in SILENT)}


def repair(state, account=None, current=None):
    """The sentence a reader has to act on. Pure."""
    if state == "crossed":
        return ("stop the deploy. The id filed under %s belongs to another "
                "account, so every call made with it lands on the wrong "
                "organization and nothing will ever error. Fix the mapping, "
                "then resolve the id at runtime so it cannot drift again."
                % (account or "this account"))
    if state == "stale":
        return ("resolve the id per account from the org's own installation "
                "route rather than storing it. The current id is %s today and "
                "will be a different one after the next reinstall."
                % (current or "on the list above"))
    if state == "gone":
        return ("the App is not installed on %s. This is not an id problem: "
                "somebody has to install it again, and your code should key "
                "state on the account login so the history survives."
                % (account or "that account"))
    if state == "current-but-reinstalled":
        return ("nothing is broken, but the id changed hands once already. "
                "Move the lookup into the code before it changes again.")
    return "nothing. This account resolves correctly."


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
    """Every installation this App currently has. Read only."""
    out = []
    for page in range(1, pages + 1):
        status, body = get(session, "/app/installations?per_page=100&page=%d" % page)
        if status != 200 or not isinstance(body, list):
            if page == 1:
                log.error("GET /app/installations returned %d; this endpoint "
                          "wants the App's JWT", status)
            break
        out.extend(body)
        if len(body) < 100:
            break
    return out


def confirm_account(session, account):
    """The current installation for one organization, straight from the API."""
    status, body = get(session, "/orgs/%s/installation" % account)
    if status != 200 or not isinstance(body, dict):
        return None
    return str(body.get("id")) if body.get("id") is not None else None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--map", default=os.environ.get("GITHUB_INSTALLATION_MAP", ""),
                    help="account=id pairs, comma separated, or a JSON object")
    ap.add_argument("--recorded-at", default=os.environ.get("GITHUB_MAP_RECORDED_AT"),
                    help="ISO date the map was last written, to spot reinstalls")
    ap.add_argument("--confirm", action="store_true",
                    help="also resolve each account's current id directly")
    args = ap.parse_args()

    jwt = os.environ.get("GITHUB_APP_JWT")
    if not jwt:
        log.error("set GITHUB_APP_JWT. The installation list is read with the "
                  "App's JWT, not with a token minted from an installation")
        return 2

    configured = parse_map(args.map)
    if not configured:
        log.error("no account=id pairs to check; pass --map or set "
                  "GITHUB_INSTALLATION_MAP")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + jwt,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    installations = list_installations(session)
    log.info("%d installation(s) visible to this App", len(installations))
    by_id = index_by_id(installations)
    by_account = index_by_account(installations)

    findings = []
    for account, ident in sorted(configured.items()):
        state, detail = drift(account, ident, by_id, by_account, args.recorded_at)
        row = {"account": account, "configured_id": ident, "state": state,
               "detail": detail, "current_id": current_id_for(account, by_account)}
        if args.confirm:
            row["confirmed_id"] = confirm_account(session, account)
        findings.append(row)

    findings.sort(key=lambda f: (f["state"] not in SILENT, f["account"]))
    for f in findings:
        if f["state"] != "current":
            log.info("%s: %s", f["state"], f["detail"])
            log.info("repair: %s", repair(f["state"], f["account"], f["current_id"]))

    extra = unmapped(by_account, configured)
    if extra:
        log.info("also installed and not in the map: %s", ", ".join(extra))

    stats = summarize(findings)
    print(json.dumps({"visible": len(installations), "summary": stats,
                      "unmapped_accounts": extra, "findings": findings},
                     indent=2, default=str))
    return 1 if stats["by_state"].get("current", 0) != stats["total"] else 0


if __name__ == "__main__":
    sys.exit(main())
