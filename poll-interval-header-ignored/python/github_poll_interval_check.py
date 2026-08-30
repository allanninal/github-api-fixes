"""Compare a configured poll interval against the floor GitHub declares.

Read only. One GET against an events endpoint, and the finding comes from its
response headers.

Events endpoints return x-poll-interval: the minimum seconds to wait before the
next poll. The feed is regenerated no faster than that, so a request underneath
it returns the page you already have.
"""
import argparse
import json
import logging
import os
import re
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_poll_interval_check")

API = "https://api.github.com"
UA = "github-poll-interval-check/1.0"

# What the events endpoints have historically returned when nothing else says
# otherwise. Used only as a last resort, and labelled as an assumption.
DEFAULT_FLOOR = 60


def parse_max_age(value):
    """Seconds from a Cache-Control header, or None. Pure."""
    match = re.search(r"max-age\s*=\s*(\d+)", str(value or ""), re.I)
    if not match:
        return None
    try:
        seconds = int(match.group(1))
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def floor_seconds(headers, default=DEFAULT_FLOOR):
    """The minimum poll interval the server declared. Pure.

    Returns (seconds, source). The source matters: "the server said 60" and
    "nothing said anything so I assumed 60" are the same number with very
    different confidence, and a report that prints only the number will be
    trusted more than it has earned.
    """
    lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
    raw = lowered.get("x-poll-interval")
    try:
        declared = int(str(raw).strip())
    except (TypeError, ValueError):
        declared = None
    if declared and declared > 0:
        return (declared, "x-poll-interval")

    age = parse_max_age(lowered.get("cache-control"))
    if age:
        return (age, "cache-control max-age")
    return (default, "documented default")


def assess(configured, floor, has_etag):
    """Compare the configured interval against the floor. Pure.

    Both directions are findings. Under the floor costs requests that cannot
    return anything new; over it costs freshness and nothing else, which is why
    only one of the two ever shows up on a quota graph.
    """
    try:
        configured = max(1, int(configured))
    except (TypeError, ValueError):
        configured = 1
    floor = max(1, int(floor or 1))

    polls = round(3600 / configured)
    allowed = round(3600 / floor)
    wasted = max(0, polls - allowed)

    if configured < floor:
        state = "under-floor"
    elif configured <= floor * 1.5:
        state = "at-floor"
    else:
        state = "over-floor"

    return {"state": state, "configured": configured, "floor": floor,
            "polls_per_hour": polls, "allowed_per_hour": allowed,
            "wasted_per_hour": wasted,
            "billable_per_hour": 0 if has_etag else wasted,
            "extra_staleness_s": max(0, configured - floor)}


def verdict(assessment):
    """Turn the comparison into a finding. Pure."""
    state = assessment.get("state")
    floor = assessment.get("floor", DEFAULT_FLOOR)
    configured = assessment.get("configured", floor)

    if state == "under-floor":
        if assessment.get("billable_per_hour"):
            return ("burning-quota",
                    "%d request(s) an hour beyond the %ds floor the server "
                    "declared, and every one of them is billable because no "
                    "etag is being sent. They return the page you already have."
                    % (assessment.get("billable_per_hour", 0), floor))
        return ("free-but-pointless",
                "%d conditional request(s) an hour beyond the %ds floor. They "
                "cost no quota, because an unchanged feed answers 304, but they "
                "cannot return anything new either: the feed is not regenerated "
                "faster than that." % (assessment.get("wasted_per_hour", 0), floor))
    if state == "over-floor":
        return ("slower-than-needed",
                "polling every %ds against a %ds floor adds up to %ds of "
                "avoidable staleness and saves nothing, because the requests "
                "you skipped would have been 304s."
                % (configured, floor, assessment.get("extra_staleness_s", 0)))
    return ("at-floor",
            "polling every %ds against a floor of %ds: nothing to reclaim in "
            "either direction." % (configured, floor))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", help="owner/name; polls that repository's events")
    ap.add_argument("--user", help="poll a user's events instead")
    ap.add_argument("--interval", type=int, default=5,
                    help="the interval your client is configured with, seconds")
    args = ap.parse_args()

    if not args.repo and not args.user:
        log.error("give --repo owner/name or --user login")
        return 2

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    path = ("/repos/%s/events" % args.repo) if args.repo else ("/users/%s/events" % args.user)
    r = requests.get(API + path, timeout=30, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })
    if r.status_code != 200:
        log.error("GET %s returned %d", path, r.status_code)
        return 2

    headers = dict(r.headers)
    lowered = {k.lower(): v for k, v in headers.items()}
    floor, source = floor_seconds(headers)
    etag = lowered.get("etag")

    log.info("%s: floor %ds (from %s), etag %s, %d event(s) on this page",
             path, floor, source, "present" if etag else "absent",
             len(r.json() if r.content else []))
    if source != "x-poll-interval":
        log.warning("x-poll-interval was not on the response, so the floor "
                    "above is an assumption. Read it per response rather than "
                    "hardcoding one: the value goes up when GitHub is busy.")

    result = assess(args.interval, floor, bool(etag))
    state, detail = verdict(result)
    log.info("%s: %s", state, detail)

    if state != "at-floor":
        log.info("repair: sleep for the value of x-poll-interval on the last "
                 "response, re-reading it every cycle, and send the etag back "
                 "as If-None-Match so an unchanged page is free.")
    if state == "slower-than-needed":
        log.info("repair: the events feed holds only a window of recent "
                 "activity, so an interval far above the floor can miss events "
                 "outright rather than merely notice them late.")

    print(json.dumps({"path": path, "floor": floor, "floor_source": source,
                      "etag": bool(etag), "assessment": result,
                      "state": state}, indent=2))
    return 1 if state in ("burning-quota", "slower-than-needed") else 0


if __name__ == "__main__":
    sys.exit(main())
