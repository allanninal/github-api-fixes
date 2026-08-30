"""Find accounts where a GitHub App was requested and never approved.

Read only. GETs against the App's own records with the App JWT, and a local
JSON file of your own connection state. This script never requests an
installation and never approves one: approving is an organization owner's
decision, and asking for one is a write. It detects the state and prints the
step for a human to take.

The point of the note: only an owner can install an App on an organization.
Anybody else going through the flow creates a *request*, which sits in a queue
until an owner approves it. Until then the App has no installation on that
account at all, while the product that started the flow shows it as connected.

What this can and cannot see: absence. GET /app/installations does not list
pending requests, and no endpoint publishes the queue to the App, so an absent
account is pending, declined, abandoned or never attempted with the same
silence. Your own record of who began a flow and when is what separates them,
which is why this script takes one as input rather than pretending a single
call answers the question.

Environment:

    GITHUB_APP_JWT    the JWT your own signing code produced
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_app_installation_pending")

API = "https://api.github.com"
UA = "github-app-installation-pending/1.0"

# How long a request can sit before it is more likely forgotten than pending.
# A default rather than a rule: it decides only which sentence is printed.
STALE_AFTER_DAYS = 7

# Said once, in one place, because it is the honest core of the note and every
# state that reports an absence has to carry it.
ABSENCE_MEANING = ("absence covers pending, declined and never started; the "
                   "API publishes no request queue, so your record is what "
                   "makes this readable.")


def read_cost(accounts, pages=1):
    """Requests this run will spend against the core quota. Pure."""
    return 1 + max(1, int(pages)) + len(accounts or [])


def installation_index(installations):
    """Index the App's installations by account login. Pure.

    Lower-cased keys because logins are compared case-insensitively everywhere
    else in this API and a record file written by hand will not match the
    casing GitHub returns.
    """
    index = {}
    for item in installations or []:
        if not isinstance(item, dict):
            continue
        account = item.get("account") or {}
        login = account.get("login") if isinstance(account, dict) else None
        if not login:
            continue
        index[str(login).strip().lower()] = {
            "id": item.get("id"),
            "created_at": item.get("created_at"),
            "repository_selection": item.get("repository_selection"),
            "suspended": item.get("suspended_at") not in (None, "", "null"),
        }
    return index


def probe_state(status):
    """What GET /orgs/{org}/installation means. Pure. (state, detail)."""
    code = int(status or 0)
    if code == 200:
        return ("installed", "the App has an installation on this account.")
    if code == 404:
        return ("no-installation",
                "the App has no installation on this account. " + ABSENCE_MEANING)
    if code in (401, 403):
        return ("unreadable",
                "the JWT was refused on this probe, so nothing can be "
                "concluded about the account.")
    return ("unclear", "HTTP %s is not one of the answers this probe gives."
            % status)


def parsed_time(text):
    """An ISO 8601 timestamp as an aware datetime, or None. Pure."""
    if not text:
        return None
    value = str(text).strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def age_days(started_at, now):
    """How long ago the flow started, in days. Pure. None if unparseable."""
    start = parsed_time(started_at)
    if start is None or now is None:
        return None
    return (now - start).total_seconds() / 86400.0


def request_age_state(days, stale_after=STALE_AFTER_DAYS):
    """Is this request plausibly still in flight. Pure. (state, detail)."""
    if days is None:
        return ("age-unknown",
                "your record does not say when the flow started, so the "
                "request cannot be aged.")
    if days <= stale_after:
        return ("awaiting-approval",
                "the flow started %.1f day(s) ago, which is recent enough that "
                "an owner may simply not have looked yet." % days)
    return ("stale-request",
            "the flow started %.1f day(s) ago. A request that old is more "
            "likely forgotten than pending, and the owner who could approve it "
            "was notified once." % days)


def reconcile(entry, installation, now, stale_after=STALE_AFTER_DAYS):
    """One account, two sources of truth. Pure. (state, detail).

    entry is your own record: account, started_at, connected. installation is
    what the App's own list holds for that account, or None.
    """
    account = str((entry or {}).get("account") or "unknown")
    connected = bool((entry or {}).get("connected"))
    started_at = (entry or {}).get("started_at")

    if installation:
        if installation.get("suspended"):
            return ("installed-but-suspended",
                    "an installation exists on %s and is suspended, which is a "
                    "different diagnosis and a different repair. Do not chase "
                    "an approval that already happened." % account)
        if connected:
            return ("agreed-connected",
                    "an installation exists and your record agrees. Nothing to "
                    "reconcile.")
        return ("unrecorded-installation",
                "an installation exists on %s and your record does not show it "
                "as connected. An owner approved it after the fact and nothing "
                "in your product noticed." % account)
    if connected:
        return ("false-connected",
                "your record says connected%s and this App has no installation "
                "on %s. %s"
                % (" since " + str(started_at) if started_at else "",
                   account, ABSENCE_MEANING))
    age_state, age_detail = request_age_state(age_days(started_at, now), stale_after)
    if age_state in ("awaiting-approval", "stale-request"):
        return (age_state, age_detail + " " + ABSENCE_MEANING)
    return ("agreed-disconnected",
            "no installation, and your record does not claim one. There is "
            "nothing here to explain.")


def actionable(state):
    """Is this a state somebody has to do something about. Pure."""
    return state in ("false-connected", "awaiting-approval", "stale-request",
                     "unrecorded-installation", "installed-but-suspended")


def printed_step(state, account):
    """The step to put in front of a human. Pure. Nothing here is executed.

    This script holds a read path only. It cannot request an installation and
    cannot approve one, and it should not: approving is a decision about what
    reaches an organization's code, and it belongs to an owner.
    """
    if state in ("false-connected", "awaiting-approval", "stale-request"):
        return ("an owner of %s has to approve the pending installation "
                "request from the organization's GitHub Apps settings. "
                "Nothing here requests or approves anything." % account)
    if state == "unrecorded-installation":
        return ("reconcile your stored connection state for %s: the "
                "installation is real and your product is ignoring it."
                % account)
    if state == "installed-but-suspended":
        return ("ask an owner of %s to unsuspend the installation. The "
                "approval is not what is missing." % account)
    return "nothing for this account."


def product_repair(states):
    """What to change in the product, given everything seen. Pure."""
    if any(s == "false-connected" for s in states):
        return ("stop rendering a completed flow as a connection. Show the "
                "requested state explicitly, prompt the user to ask an owner "
                "to approve it, and reconcile against GET /app/installations "
                "on a schedule rather than trusting the callback.")
    if any(s in ("awaiting-approval", "stale-request") for s in states):
        return ("surface the pending state in the product and re-check it on a "
                "schedule. A request that nobody is reminded about is a "
                "request that expires by neglect.")
    if any(s == "unrecorded-installation" for s in states):
        return ("reconcile in the other direction too: an installation "
                "approved after the user gave up delivers nothing if your "
                "product never records it.")
    return "nothing. The App's installations and your record agree."


def load_record(path, accounts):
    """Your own connection state. Not part of the API and not a write.

    Accepts a JSON list of {account, started_at, connected}. Accounts named on
    the command line are added as ones you believe are connected, which is the
    common case: somebody says a customer is connected and you want to know.
    """
    entries = []
    if path:
        with open(path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        for item in loaded if isinstance(loaded, list) else []:
            if isinstance(item, dict) and item.get("account"):
                entries.append(item)
    for account in accounts or []:
        entries.append({"account": account, "connected": True})
    return entries


def get(session, path):
    """One GET. Returns the response object."""
    return session.get(API + path, timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record",
                        help="JSON list of {account, started_at, connected}")
    parser.add_argument("--account", action="append", default=[],
                        help="an account you believe is connected; repeatable")
    parser.add_argument("--max-pages", type=int, default=5,
                        help="pages of /app/installations to read")
    parser.add_argument("--stale-after", type=int, default=STALE_AFTER_DAYS,
                        help="days after which a request is called stale")
    args = parser.parse_args()

    jwt = os.environ.get("GITHUB_APP_JWT")
    if not jwt:
        log.error("set GITHUB_APP_JWT (the JWT your own signing code produced)")
        return 2

    entries = load_record(args.record, args.account)
    if not entries:
        log.error("nothing to reconcile: pass --record or --account. This "
                  "script compares GitHub's list against your own, and the "
                  "second half is the half GitHub cannot supply.")
        return 2

    log.info("read cost: up to %d request(s) against the core quota "
             "(1 app + up to %d list page(s) + %d account probe(s))",
             read_cost(entries, args.max_pages), max(1, args.max_pages),
             len(entries))

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + jwt,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    app = get(session, "/app")
    if app.status_code != 200:
        log.error("GET /app returned HTTP %s: the JWT was not accepted, which "
                  "is a different note", app.status_code)
        return 2
    log.info("app: %s (JWT accepted)", (app.json() or {}).get("slug"))

    installations, pages = [], 0
    for page in range(1, max(1, args.max_pages) + 1):
        response = get(session, "/app/installations?per_page=100&page=%d" % page)
        if response.status_code != 200:
            log.warning("installation list page %d returned HTTP %s; the list "
                        "below is partial", page, response.status_code)
            break
        batch = response.json() or []
        pages = page
        installations.extend(batch)
        if len(batch) < 100:
            break
    log.info("installations: %d read from %d page(s)", len(installations), pages)

    index = installation_index(installations)
    now = datetime.now(timezone.utc)
    results, states = [], []

    for entry in entries:
        account = str(entry.get("account"))
        probe = get(session, "/orgs/%s/installation" % account)
        probe_result, probe_detail = probe_state(probe.status_code)
        installation = index.get(account.strip().lower())
        if probe_result == "installed" and installation is None:
            installation = {"id": None, "created_at": None,
                            "repository_selection": None, "suspended": False}
        if probe_result == "no-installation":
            installation = None
        state, detail = reconcile(entry, installation, now, args.stale_after)
        log.info("%s: HTTP %s from GET /orgs/%s/installation",
                 account, probe.status_code, account)
        log.info("  %s — %s", state, detail)
        log.info("  step: %s", printed_step(state, account))
        states.append(state)
        results.append({
            "account": account,
            "probe_status": probe.status_code,
            "probe_state": probe_result,
            "probe_detail": probe_detail,
            "installation_id": (installation or {}).get("id"),
            "state": state,
            "detail": detail,
            "actionable": actionable(state),
            "step": printed_step(state, account),
        })

    log.info("product repair: %s", product_repair(states))
    print(json.dumps({
        "installations_read": len(installations),
        "pages_read": pages,
        "accounts": results,
        "product_repair": product_repair(states),
    }, indent=2, default=str))
    return 1 if any(actionable(s) for s in states) else 0


if __name__ == "__main__":
    sys.exit(main())
