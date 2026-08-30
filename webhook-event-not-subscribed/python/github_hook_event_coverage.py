"""Compare the events a webhook is subscribed to against the ones you handle.

Read only. Two GETs per hook: the hook list, and one page of its delivery log to
see which events really arrived. An unsubscribed event produces no failure and no
delivery record, so the only way to find one is to compare two lists.
"""
import argparse
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_hook_event_coverage")

API = "https://api.github.com"
UA = "github-hook-event-coverage/1.0"


def normalize(name):
    """Canonical form of an event name. Pure.

    Three spellings reach this function and none of them is always right. GitHub
    names events with underscores (pull_request), URLs use hyphens, and a handler
    is often registered under an action (pull_request.opened) which is a field
    inside the payload rather than something a hook can subscribe to.
    """
    base = str(name or "").strip().lower().replace("-", "_")
    if "." in base:
        base = base.split(".", 1)[0]
    return base


def coverage(handled, subscribed, seen=()):
    """Compare handlers, subscriptions and observed traffic. Pure.

    Returns a list of rows, one per event on either side, each with a state:

      missing    subscribed nowhere, so the handler can never run
      delivered  subscribed and seen in the delivery window
      quiet      subscribed but not seen, which may just mean nothing happened
      wildcard   the hook subscribes to everything, including future events
      unhandled  arriving or subscribed with no handler behind it
    """
    subs = {}
    wildcard = False
    for raw in subscribed or []:
        if str(raw).strip() == "*":
            wildcard = True
            continue
        subs[normalize(raw)] = str(raw)

    seen_events = {}
    for raw in seen or []:
        key = normalize(raw)
        seen_events[key] = seen_events.get(key, 0) + 1

    rows = []
    claimed = set()
    for raw in handled or []:
        key = normalize(raw)
        claimed.add(key)
        note = ""
        if str(raw) != key:
            note = "your handler is registered as %r; GitHub spells this %r" % (
                str(raw), key)
        if wildcard:
            state = "wildcard"
        elif key not in subs:
            state = "missing"
        elif key in seen_events:
            state = "delivered"
        else:
            state = "quiet"
        rows.append({"event": key, "handler": str(raw), "state": state,
                     "seen": seen_events.get(key, 0), "note": note})

    for key in sorted(set(subs) | set(seen_events)):
        if key in claimed:
            continue
        rows.append({"event": key, "handler": None, "state": "unhandled",
                     "seen": seen_events.get(key, 0),
                     "note": "subscribed" if key in subs else "arriving without a subscription"})
    return rows


def next_link(response):
    """The rel=next URL from the Link header, or None."""
    for part in (response.headers.get("Link") or "").split(","):
        chunk = part.strip()
        if chunk.startswith("<") and chunk.endswith('rel="next"'):
            return chunk[1:chunk.index(">")]
    return None


def get(session, url, **params):
    r = session.get(url, params=params, timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, expired or "
                         "malformed")
    if r.status_code in (403, 404):
        raise SystemExit("%d from %s: reading hooks needs admin:repo_hook, and "
                         "GitHub answers 404 rather than 403 when the token "
                         "cannot see the resource at all" % (r.status_code, url))
    r.raise_for_status()
    return r


def page(session, url, limit=500, **params):
    out = []
    while url and len(out) < limit:
        r = get(session, url, **params)
        out.extend(r.json())
        url, params = next_link(r), {}
    return out[:limit]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--handles", action="append", default=[],
                    help="an event your receiver implements; repeat per event")
    ap.add_argument("--max-deliveries", type=int, default=200,
                    help="deliveries to read per hook when collecting the "
                         "events that really arrived")
    args = ap.parse_args()

    if not args.handles:
        log.error("pass --handles once per event your receiver implements, "
                  "taken from its switch on X-GitHub-Event")
        return 2

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    owner, _, name = args.repo.partition("/")
    if not (owner and name):
        log.error("--repo takes owner/name, for example acme/api")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    base = "%s/repos/%s/%s/hooks" % (API, owner, name)
    hooks = page(session, base, per_page=100)
    if not hooks:
        log.info("no webhooks on %s that this token can see", args.repo)
        return 0

    missing = unhandled = 0
    for hook in hooks:
        hid = hook.get("id")
        url = (hook.get("config") or {}).get("url", "?")
        subscribed = hook.get("events") or []
        deliveries = page(session, "%s/%s/deliveries" % (base, hid),
                          limit=args.max_deliveries, per_page=100)
        seen = [d.get("event") for d in deliveries]
        log.info("hook %s %s  subscribes to %d event(s), %d delivery(ies) read",
                 hid, url, len(subscribed), len(deliveries))

        for row in coverage(args.handles, subscribed, seen):
            line = "  %-10s %s%s" % (row["state"], row["event"],
                                     "  " + row["note"] if row["note"] else "")
            if row["state"] in ("delivered", "quiet"):
                log.info(line)
                continue
            log.warning(line)
            if row["state"] == "missing":
                missing += 1
                log.warning("     repair: add %r to this hook's events array; "
                            "until then the handler cannot run and nothing will "
                            "report an error", row["event"])
            elif row["state"] == "unhandled":
                unhandled += 1
                log.warning("     %d delivery(ies) of an event nothing handles: "
                            "volume you receive, verify and discard",
                            row["seen"])
            elif row["state"] == "wildcard":
                log.warning("     the hook subscribes to *, so this arrives "
                            "along with every event type GitHub adds in future")

    log.info("%d hook(s), %d handler(s) with no subscription, %d unhandled "
             "event(s) arriving", len(hooks), missing, unhandled)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
