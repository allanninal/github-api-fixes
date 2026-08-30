"""Find GitHub App installations still living on an older permission grant.

Read only. Two GETs with the App's JWT: the App's own declaration and the list
of installations. Nothing is granted, accepted, upgraded or changed. Accepting
a permission upgrade is a human act performed by an account owner, so the
script prints who has to be asked and for what.

Editing an App's permissions does not apply the change to installations that
already exist. Each installation keeps the grant it accepted until an owner
accepts the new one, so the App declaration and the installation grant are two
different objects that drift apart the moment the App is edited.

Environment:

    GITHUB_APP_JWT   the JWT your own signing code produced
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_permission_upgrade_lag")

API = "https://api.github.com"
UA = "github-permission-upgrade-lag/1.0"

# Permission levels are ordered, not a set. An installation that granted read
# where the App declares write is behind, and a check that only compares keys
# calls it current and then wonders why the writes keep failing.
RANK = {"none": 0, "read": 1, "write": 2, "admin": 3}


def rank(level):
    """A permission level as a comparable integer. Pure.

    Anything unrecognised sorts as none rather than raising, because a level
    this script has not heard of is not evidence that access was granted.
    """
    return RANK.get(str(level or "none").strip().lower(), 0)


def permission_gap(declared, granted):
    """Declared permissions an installation holds at a lower level. Pure.

    Returns [(permission, declared_level, granted_level), ...] sorted by name.
    granted_level is the literal string "absent" where the key is missing, so
    the report can say which of the two shapes it found.
    """
    out = []
    for name, wanted in sorted((declared or {}).items()):
        have = (granted or {}).get(name)
        if rank(have) < rank(wanted):
            out.append((name, str(wanted), str(have) if have else "absent"))
    return out


def permission_surplus(declared, granted):
    """Permissions an installation holds beyond what the App declares. Pure."""
    out = []
    for name, have in sorted((granted or {}).items()):
        wanted = (declared or {}).get(name)
        if rank(have) > rank(wanted):
            out.append((name, str(wanted) if wanted else "not declared", str(have)))
    return out


def event_gap(declared_events, granted_events):
    """Declared events an installation has not accepted. Pure."""
    have = {str(e).strip().lower() for e in (granted_events or [])}
    return sorted({str(e).strip().lower() for e in (declared_events or [])} - have)


def classify(declared_permissions, declared_events, inst):
    """Sort one installation against the App declaration. Pure.

    Returns a row: account, id, state, and the three diffs. The state is
    upgrade-pending when anything declared is not held, grant-ahead when the
    installation holds more than the App declares and nothing less, and
    current when the two maps agree.
    """
    inst = inst if isinstance(inst, dict) else {}
    account = inst.get("account") or {}
    gaps = permission_gap(declared_permissions, inst.get("permissions"))
    extra = permission_surplus(declared_permissions, inst.get("permissions"))
    events = event_gap(declared_events, inst.get("events"))
    if gaps or events:
        state = "upgrade-pending"
    elif extra:
        state = "grant-ahead"
    else:
        state = "current"
    return {"installation_id": inst.get("id"),
            "account": account.get("login") if isinstance(account, dict) else None,
            "state": state, "permission_gap": gaps,
            "permission_surplus": extra, "event_gap": events}


def verdict(rows):
    """Turn the per-installation rows into one finding. Pure."""
    rows = rows or []
    if not rows:
        return ("no-installations",
                "this App has no installations, so there is nothing to be "
                "behind. Nothing here is evidence about permissions.")
    behind = [r for r in rows if r["state"] == "upgrade-pending"]
    if behind:
        return ("upgrades-pending",
                "%d of %d installation(s) are behind the App declaration. "
                "Their tokens carry the permission map they accepted, not the "
                "one the App settings page shows."
                % (len(behind), len(rows)))
    ahead = [r for r in rows if r["state"] == "grant-ahead"]
    if ahead:
        return ("grants-ahead",
                "%d of %d installation(s) hold more than the App declares, "
                "which happens after a permission is removed rather than "
                "added. Nothing is failing; the access is simply unused."
                % (len(ahead), len(rows)))
    return ("all-current",
            "all %d installation(s) have accepted what the App declares."
            % len(rows))


def cohorts(rows):
    """Group the laggards by exactly what they are missing. Pure.

    A hundred rows usually collapse to two or three, and the cohorts are the
    versions of the App people accepted. That is the pattern which looked
    random from inside a single 403.
    """
    out = {}
    for row in rows or []:
        if row["state"] != "upgrade-pending":
            continue
        key = ", ".join("%s %s (declared %s)" % (n, g, d)
                        for n, d, g in row["permission_gap"]) or "events only"
        out.setdefault(key, []).append(row["account"] or row["installation_id"])
    return {k: sorted(map(str, v)) for k, v in sorted(out.items())}


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
                log.error("GET /app/installations returned %d; this endpoint "
                          "wants the App JWT", status)
            break
        out.extend(body)
        if len(body) < 100:
            break
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--account", default=None,
                    help="report on one account login only")
    args = ap.parse_args()

    jwt = os.environ.get("GITHUB_APP_JWT")
    if not jwt:
        log.error("set GITHUB_APP_JWT to the JWT your own signing code "
                  "produced. Both reads here are App-level and neither one "
                  "accepts an installation token")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + jwt,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    status, app = get(session, "/app")
    if status != 200 or not isinstance(app, dict):
        log.error("GET /app returned %d, so there is no declaration to "
                  "compare against", status)
        return 2
    declared_permissions = app.get("permissions") or {}
    declared_events = app.get("events") or []
    log.info("app declares %d permission(s) and %d event(s)",
             len(declared_permissions), len(declared_events))

    installations = list_installations(session)
    rows = [classify(declared_permissions, declared_events, i) for i in installations]
    if args.account:
        rows = [r for r in rows if r["account"] == args.account]

    state, detail = verdict(rows)
    log.info("%s: %s", state, detail)
    for row in rows:
        if row["state"] == "upgrade-pending":
            for name, want, have in row["permission_gap"]:
                log.info("  %s %s: %s %s, declared %s",
                         row["installation_id"], row["account"], name, have, want)
            if row["event_gap"]:
                log.info("  %s %s: events not accepted: %s",
                         row["installation_id"], row["account"],
                         ", ".join(row["event_gap"]))
        if row["state"] == "grant-ahead":
            for name, want, have in row["permission_surplus"]:
                log.info("  %s %s holds %s %s, %s",
                         row["installation_id"], row["account"], name, have, want)

    if state == "upgrades-pending":
        log.info("repair: an owner on each account accepts the pending "
                 "permission request from that org's Installed GitHub Apps "
                 "page. Until then, branch on the installation's own "
                 "permission map rather than on the App declaration")
    elif state == "grants-ahead":
        log.info("repair: nothing urgent. Those installations carry access "
                 "the App no longer declares, which is a tidy-up rather than "
                 "an outage")

    print(json.dumps({"declared_permissions": declared_permissions,
                      "declared_events": sorted(str(e) for e in declared_events),
                      "state": state, "cohorts": cohorts(rows),
                      "installations": rows}, indent=2, default=str))
    return 1 if state == "upgrades-pending" else 0


if __name__ == "__main__":
    sys.exit(main())
