"""Find webhook events a GitHub App's handlers wait for and never receive.

Read only. Two GETs with the App's JWT: the App's own record and its recent
webhook deliveries. Nothing is subscribed, permitted or changed. Subscribing is
an edit to the App and then a human acceptance on every installation, so the
script prints the three steps in the order they have to happen.

A GitHub App receives only the events it declares, and it can only declare an
event whose gating permission it holds. When the permission is absent the
subscription checkbox is not offered at all, so the subscription silently never
happens and nothing anywhere records the attempt.

This is the App-side case. A repository or organization webhook has its own
events list, read from the repository hooks endpoint, which is a different
object with a different repair.

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
log = logging.getLogger("github_app_event_subscriptions")

API = "https://api.github.com"
UA = "github-app-event-subscriptions/1.0"

# Which App permission gates which event. Nothing in the API returns this, so
# the table is written from the documentation rather than fetched, and an event
# that is not in it is reported as unknown rather than guessed at. A wrong
# answer here sends somebody to request a permission they do not need.
EVENT_PERMISSION = {
    "check_run": "checks",
    "check_suite": "checks",
    "commit_comment": "contents",
    "create": "contents",
    "delete": "contents",
    "deployment": "deployments",
    "deployment_status": "deployments",
    "fork": "metadata",
    "issue_comment": "issues",
    "issues": "issues",
    "label": "metadata",
    "member": "members",
    "membership": "members",
    "milestone": "issues",
    "organization": "members",
    "public": "metadata",
    "pull_request": "pull_requests",
    "pull_request_review": "pull_requests",
    "pull_request_review_comment": "pull_requests",
    "pull_request_review_thread": "pull_requests",
    "push": "contents",
    "release": "contents",
    "repository": "metadata",
    "repository_dispatch": "contents",
    "star": "metadata",
    "status": "statuses",
    "team_add": "members",
    "watch": "metadata",
    "workflow_dispatch": "actions",
    "workflow_job": "actions",
    "workflow_run": "actions",
}

# Every event carries metadata implicitly, so an App that holds nothing still
# holds this one. Listing it keeps the "not permitted" branch honest.
ALWAYS_HELD = ("metadata",)


def normalize(event):
    """An event name reduced to the form GitHub spells it in. Pure.

    Case and surrounding whitespace only. A genuinely misspelled name is left
    misspelled so it stays visible as unknown, because quietly correcting it
    would hide the actual mistake in a report about missing events.
    """
    return str(event or "").strip().lower()


def gating_permission(event):
    """The App permission that gates an event, or None if unknown. Pure."""
    return EVENT_PERMISSION.get(normalize(event))


def holds(permissions, name):
    """Whether the App holds a permission at read or better. Pure."""
    if name in ALWAYS_HELD:
        return True
    value = (permissions or {}).get(name)
    return bool(value) and str(value).strip().lower() != "none"


def seen_events(deliveries):
    """Distinct event names in a delivery log page. Pure."""
    out = set()
    for row in deliveries or []:
        if isinstance(row, dict) and row.get("event"):
            out.add(normalize(row["event"]))
    return out


def subscription_state(event, subscribed, permissions, seen=None):
    """Sort one handled event into a state. Pure.

    subscribed is the App's events array, permissions its permission map, seen
    the distinct events observed in the delivery log. seen only ever refines a
    positive answer: an event that has not arrived may simply not have
    happened, so its absence is never a finding on its own.
    """
    name = normalize(event)
    declared = {normalize(e) for e in (subscribed or [])}
    gate = gating_permission(name)
    if name in declared:
        if seen is not None and name in seen:
            return ("subscribed-and-arriving",
                    "%s is declared by the App and has arrived recently." % name)
        return ("subscribed-not-yet-seen",
                "%s is declared by the App but has not arrived in the "
                "retention window, which usually means it has not happened "
                "rather than that it is broken." % name)
    if gate is None:
        return ("not-subscribed-gate-unknown",
                "%s is not declared by the App, so it has never been "
                "delivered. This script does not know which permission gates "
                "it; check the published event list before requesting one."
                % name)
    if not holds(permissions, gate):
        return ("not-subscribed-blocked",
                "%s is not declared, and the %s permission that gates it is "
                "not held. The subscription cannot be ticked until the "
                "permission is added." % (name, gate))
    return ("not-subscribed-permitted",
            "%s is not declared, but the %s permission that gates it is held, "
            "so subscribing is an edit to the App followed by an acceptance "
            "round." % (name, gate))


def rows(handled, subscribed, permissions, seen=None):
    """One row per handled event, in the order they were given. Pure."""
    out = []
    for event in handled or []:
        state, detail = subscription_state(event, subscribed, permissions, seen)
        out.append({"event": normalize(event), "state": state, "detail": detail,
                    "gated_by": gating_permission(event)})
    return out


def verdict(report):
    """Turn the rows into one finding. Pure."""
    report = report or []
    if not report:
        return ("nothing-handled",
                "no handled events were supplied, so there is nothing to "
                "compare the App's subscriptions against.")
    unreachable = [r for r in report if r["state"].startswith("not-subscribed")]
    if unreachable:
        return ("handlers-unreachable",
                "%d of %d handled event(s) can never fire, because the App "
                "does not declare them." % (len(unreachable), len(report)))
    quiet = [r for r in report if r["state"] == "subscribed-not-yet-seen"]
    if quiet:
        return ("all-subscribed-some-quiet",
                "every handled event is declared. %d of them has not arrived "
                "in the retention window, which is not by itself a fault."
                % len(quiet))
    return ("all-subscribed",
            "every handled event is declared by the App and arriving.")


def repair_steps(report):
    """The ordered repair, as lines. Pure.

    Order matters and is the point of the note: add the permission, subscribe,
    then have installations accept. Doing only the last is the usual mistake.
    """
    blocked = sorted({r["gated_by"] for r in report or []
                      if r["state"] == "not-subscribed-blocked" and r["gated_by"]})
    missing = sorted({r["event"] for r in report or []
                      if r["state"].startswith("not-subscribed")})
    if not missing:
        return []
    steps = []
    if blocked:
        steps.append("add the %s permission to the App; until then the "
                     "subscription cannot be selected at all"
                     % ", ".join(blocked))
    steps.append("subscribe the App to %s" % ", ".join(missing))
    steps.append("have an owner on every installation accept the resulting "
                 "permission request, or the event will arrive from some "
                 "accounts and not others")
    return steps


def get(session, path):
    """One GET. Returns (status, json-or-None)."""
    url = API + path if path.startswith("/") else path
    r = session.get(url, timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--handles", default="",
                    help="comma-separated event names your handlers implement, "
                         "spelled the way GitHub spells them")
    args = ap.parse_args()

    jwt = os.environ.get("GITHUB_APP_JWT")
    if not jwt:
        log.error("set GITHUB_APP_JWT to the JWT your own signing code "
                  "produced. The App's events array is on the App record, "
                  "which an installation token cannot read")
        return 2

    handled = [h for h in (p.strip() for p in args.handles.split(",")) if h]
    if not handled:
        log.error("pass --handles with the event names your receiver "
                  "implements; without them there is nothing to compare")
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
        log.error("GET /app returned %d, so the App's subscriptions cannot be "
                  "read", status)
        return 2
    subscribed = app.get("events") or []
    permissions = app.get("permissions") or {}
    log.info("app subscribes to %d event(s), holds %d permission(s)",
             len(subscribed), len(permissions))

    seen = None
    d_status, deliveries = get(session, "/app/hook/deliveries?per_page=100")
    if d_status == 200 and isinstance(deliveries, list):
        seen = seen_events(deliveries)
        log.info("delivery log shows %d distinct event(s) in the retention "
                 "window", len(seen))
    else:
        log.info("delivery log unavailable (%d); the subscription answer does "
                 "not depend on it", d_status)

    report = rows(handled, subscribed, permissions, seen)
    state, detail = verdict(report)
    log.info("%s: %s", state, detail)
    for row in report:
        if row["state"] != "subscribed-and-arriving":
            log.info("  %s", row["detail"])

    for i, line in enumerate(repair_steps(report), start=1):
        log.info("repair step %d: %s", i, line)

    print(json.dumps({"subscribed": sorted(normalize(e) for e in subscribed),
                      "permissions": permissions,
                      "seen_in_deliveries": sorted(seen) if seen is not None else None,
                      "state": state, "events": report}, indent=2, default=str))
    return 1 if state == "handlers-unreachable" else 0


if __name__ == "__main__":
    sys.exit(main())
