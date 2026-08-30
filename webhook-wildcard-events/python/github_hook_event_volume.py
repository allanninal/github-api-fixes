"""Quantify what a wildcard webhook subscription costs a receiver.

Read only. GETs the repository's hooks and their delivery records, tallies the
deliveries by event type, and reports the fraction that came from events the
receiver does not implement. Nothing is created, edited or deleted: the script
prints the explicit events list to install in place of the wildcard.

A hook configured with ["*"] receives every event type GitHub has and every one
it adds afterwards. Nothing fails. The cost is volume, paid on every delivery
before the handler can decide it does not want it, and it grows on GitHub's
release schedule rather than yours.

This is the opposite comparison to an unsubscribed-event check. That one looks
for events in your code that are missing from the hook. This one looks for
events on the hook that are missing from your code.

Environment:

    GITHUB_TOKEN            a read-only token with access to the repository
    GITHUB_HANDLED_EVENTS   comma separated events your receiver implements
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_hook_event_volume")

API = "https://api.github.com"
UA = "github-hook-event-volume/1.0"

WILDCARD = "*"


def normalize(name):
    """One event name, lowercased and trimmed. Pure.

    Deliberately narrow: case and surrounding space only. A genuinely misspelled
    event name should stay visible as itself rather than be quietly corrected
    into something that looks subscribed.
    """
    return str(name or "").strip().lower()


def subscribed(hook):
    """The normalised events array on a hook. Pure."""
    if not isinstance(hook, dict):
        return []
    events = hook.get("events")
    if not isinstance(events, list):
        return []
    return [e for e in (normalize(x) for x in events) if e]


def is_wildcard(events):
    """Whether this subscription is open ended. Pure."""
    return WILDCARD in {normalize(e) for e in (events or [])}


def handled_set(names):
    """The events a receiver implements, as a normalised set. Pure."""
    if isinstance(names, str):
        names = names.replace(";", ",").split(",")
    return {e for e in (normalize(n) for n in (names or [])) if e and e != WILDCARD}


def tally(rows):
    """Deliveries by event type. Pure."""
    counts = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        event = normalize(row.get("event")) or "unknown"
        counts[event] = counts.get(event, 0) + 1
    return counts


def waste(counts, handled):
    """How much of the delivered volume the receiver discards. Pure."""
    counts = counts or {}
    handled = handled_set(handled)
    total = sum(counts.values())
    unwanted = {e: n for e, n in counts.items() if e not in handled}
    discarded = sum(unwanted.values())
    return {"total": total,
            "unhandled_deliveries": discarded,
            "unhandled_events": sorted(unwanted),
            "share": round(100.0 * discarded / total, 1) if total else 0.0}


def proposed_events(handled):
    """The explicit events list to install in place of the wildcard. Pure.

    Built from what the receiver implements rather than from what happened to
    arrive. An event that has not fired during the retained window is not an
    event you do not need, and pruning on observation alone is how a release
    handler stops working three months later.
    """
    return sorted(handled_set(handled))


def never_seen(counts, handled):
    """Handled events with no deliveries in the window. Pure.

    Reported as a caution, never as a reason to drop them.
    """
    counts = counts or {}
    return sorted(e for e in handled_set(handled) if not counts.get(e))


def verdict(events, counts, handled):
    """Turn the subscription and the volume into a finding. Pure."""
    subs = {e for e in (normalize(x) for x in (events or [])) if e}
    wanted = handled_set(handled)
    if WILDCARD in subs:
        w = waste(counts, wanted)
        if not w["total"]:
            return ("wildcard-unmeasured",
                    "this hook subscribes to every event with *, and no "
                    "deliveries in the retained window let the volume be "
                    "measured. The subscription is open ended either way: "
                    "every event type GitHub ships next joins it.")
        if w["unhandled_deliveries"]:
            return ("wildcard",
                    "%d of %d deliveries (%.1f%%) were events this receiver "
                    "does not implement, and * also subscribes to every event "
                    "type GitHub ships next."
                    % (w["unhandled_deliveries"], w["total"], w["share"]))
        return ("wildcard-all-handled",
                "every delivery in the window happened to be an event this "
                "receiver implements, which is luck rather than design: * "
                "subscribes to event types that do not exist yet.")
    extra = sorted(subs - wanted)
    if extra:
        return ("over-subscribed",
                "this hook subscribes to %d event(s) the receiver does not "
                "implement: %s." % (len(extra), ", ".join(extra)))
    if not subs:
        return ("no-events",
                "this hook has an empty events array, so nothing is delivered "
                "to it at all.")
    return ("tight", "every subscribed event is one the receiver implements.")


def repair(state, handled=None, counts=None):
    """The sentence a reader has to act on. Pure."""
    listing = json.dumps(proposed_events(handled))
    if state in ("wildcard", "wildcard-unmeasured", "wildcard-all-handled"):
        caution = ""
        pending = never_seen(counts, handled)
        if pending:
            caution = (" Keep %s on the list even though nothing arrived for "
                       "them in this window." % ", ".join(pending))
        return ("replace [\"*\"] with %s, which bounds the subscription and "
                "stops new event types joining it without a decision.%s"
                % (listing, caution))
    if state == "over-subscribed":
        return ("narrow the events array to %s. Nothing is failing; this is "
                "volume the receiver pays for and discards." % listing)
    if state == "no-events":
        return ("add the events the receiver implements: %s. An empty array "
                "delivers nothing." % listing)
    return "nothing. The subscription matches what the receiver handles."


def next_link(headers):
    """The rel=next URL from a Link header, or None. Pure."""
    link = (headers or {}).get("Link") or (headers or {}).get("link") or ""
    for part in str(link).split(","):
        url = part.split(";")[0].strip()
        if 'rel="next"' in part and url.startswith("<") and url.endswith(">"):
            return url[1:-1]
    return None


def get(session, url):
    """One GET. Returns (status, json-or-None, headers)."""
    full = API + url if url.startswith("/") else url
    r = session.get(full, timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body, r.headers


def deliveries(session, owner, repo, hook_id, pages=8):
    """Delivery records for one hook, following the cursor. Read only."""
    url = "/repos/%s/%s/hooks/%s/deliveries?per_page=100" % (owner, repo, hook_id)
    out = []
    for _ in range(pages):
        status, body, headers = get(session, url)
        if status != 200 or not isinstance(body, list):
            log.error("deliveries for hook %s returned %d", hook_id, status)
            break
        out.extend(body)
        url = next_link(headers)
        if not url:
            break
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="owner/repo")
    ap.add_argument("--handles", default=os.environ.get("GITHUB_HANDLED_EVENTS", ""),
                    help="comma separated events the receiver implements")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN to a read-only token that can read the "
                  "repository's hooks")
        return 2
    if "/" not in args.repo:
        log.error("--repo takes owner/repo")
        return 2
    handled = handled_set(args.handles)
    if not handled:
        log.error("pass --handles with the events your receiver implements; "
                  "without them there is nothing to compare the hook against")
        return 2
    owner, repo = args.repo.split("/", 1)

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    status, hooks, _ = get(session, "/repos/%s/%s/hooks?per_page=100" % (owner, repo))
    if status != 200 or not isinstance(hooks, list):
        log.error("GET hooks returned %d", status)
        return 2

    findings = []
    for hook in hooks:
        hook_id = hook.get("id")
        url = (hook.get("config") or {}).get("url", "?")
        events = subscribed(hook)
        log.info("hook %s -> %s", hook_id, url)
        log.info("subscribed: %s", "* (wildcard)" if is_wildcard(events)
                 else ", ".join(events) or "nothing")
        rows = deliveries(session, owner, repo, hook_id)
        counts = tally(rows)
        log.info("%d deliveries in the window across %d event type(s)",
                 sum(counts.values()), len(counts))
        state, detail = verdict(events, counts, handled)
        log.info("%s: %s", state, detail)
        log.info("repair: %s", repair(state, handled, counts))
        findings.append({"hook_id": hook_id, "url": url, "events": events,
                         "wildcard": is_wildcard(events), "state": state,
                         "detail": detail, "counts": counts,
                         "waste": waste(counts, handled),
                         "proposed_events": proposed_events(handled),
                         "handled_but_unseen": never_seen(counts, handled)})

    print(json.dumps({"repo": args.repo, "handled": sorted(handled),
                      "hooks": findings}, indent=2, default=str))
    return 1 if any(f["state"] != "tight" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
