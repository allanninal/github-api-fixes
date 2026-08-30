"""Compute the wait a throttled GitHub response asks for, and cost your retries.

Read only. The single live request is a GET against /rate_limit, which does not
count against the primary rate limit. Everything that decides the wait is a pure
function of the response headers, so a response captured during an incident can
be analysed later with --status and --header.

Whether your client honours these headers is not visible through the API: it
lives in your code. What is visible is the contract, and what it costs to ignore.
"""
import argparse
import logging
import os
import sys
import time
from email.utils import parsedate_to_datetime

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_backoff_plan")

API = "https://api.github.com"
UA = "github-backoff-plan/1.0"

# Where a secondary limit sends no retry-after, the documented advice is to wait
# at least a minute before trying again.
SECONDARY_FLOOR_SECONDS = 60.0


def retry_after_seconds(value, now):
    """Parse a retry-after header into seconds from now, or None. Pure.

    HTTP allows either a delay in seconds or an HTTP-date. GitHub sends seconds,
    but a proxy in front of the client is free to rewrite it into the other form,
    and a parser that only does int() treats that as absent and falls through to
    a default that is usually far too short.
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return max(0.0, float(int(text)))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    return max(0.0, when.timestamp() - float(now))


def required_wait(status, headers, now):
    """How long a correct client sleeps before its next request. Pure.

    Returns (seconds, source, detail). The order is not arbitrary: a secondary
    limit can fire while the primary bucket is untouched, so retry-after has to
    win over the reset timestamp, which in that case is describing an hour that
    has nothing to do with why this request was refused.
    """
    lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
    try:
        status = int(status)
    except (TypeError, ValueError):
        status = 0

    if status not in (403, 429):
        return (0.0, "none",
                "%d is not a throttled response, so there is nothing to wait for"
                % status)

    seconds = retry_after_seconds(lowered.get("retry-after"), now)
    if seconds is not None:
        return (seconds, "retry-after",
                "the response asked for %.0f second(s). Sleep exactly that, not "
                "a capped or scaled version of it." % seconds)

    try:
        remaining = int(lowered.get("x-ratelimit-remaining"))
    except (TypeError, ValueError):
        remaining = None
    try:
        reset = float(lowered.get("x-ratelimit-reset"))
    except (TypeError, ValueError):
        reset = None

    if remaining == 0 and reset is not None:
        return (max(0.0, reset - float(now)), "x-ratelimit-reset",
                "the hourly quota is spent and returns at the reset timestamp, "
                "%.0f second(s) from now" % max(0.0, reset - float(now)))

    return (SECONDARY_FLOOR_SECONDS, "floor",
            "no retry-after and the primary bucket is not empty, so this is a "
            "secondary limit that sent no wait. Treat %.0f seconds as the floor "
            "and back off exponentially from there."
            % SECONDARY_FLOOR_SECONDS)


def backoff(attempt, base=1.0, cap=60.0):
    """Exponential delay for a given attempt number. Pure, and unjittered.

    The fallback for when the server said nothing at all. Jitter is applied by the
    caller rather than in here, so that the schedule this returns is something the
    tests can assert on and a reader can predict.
    """
    attempt = max(0, int(attempt))
    return min(float(cap), float(base) * (2 ** attempt))


def wasted_requests(seconds, interval):
    """How many refused requests a fixed-interval retrier fits in the wait. Pure.

    This is the number that makes the argument. Every one of these is sent into a
    limit that is already engaged, and on a secondary limit each one is fresh
    evidence of the burst behaviour being throttled.
    """
    seconds = max(0.0, float(seconds))
    interval = float(interval)
    if interval <= 0:
        return 0
    return int(seconds // interval)


def plan(status, headers, now, interval=1.0):
    """Turn a throttled response into a finding. Pure. Returns (state, report)."""
    seconds, source, detail = required_wait(status, headers, now)
    wasted = wasted_requests(seconds, interval)
    report = {"wait_seconds": round(seconds, 1), "source": source,
              "detail": detail, "wasted_requests": wasted,
              "retry_interval": interval,
              "fallback_schedule": [backoff(i) for i in range(5)]}

    if source == "none":
        return ("not-throttled", report)
    if wasted >= 60:
        return ("hammering", report)
    if wasted > 0:
        return ("impatient", report)
    return ("honoured", report)


def parse_header(text):
    """'Name: value' from the command line into a (name, value) pair."""
    name, _, value = str(text).partition(":")
    return (name.strip(), value.strip())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--status", type=int, default=None,
                    help="analyse a captured response with this status instead "
                         "of probing the API")
    ap.add_argument("--header", action="append", default=[],
                    help="'name: value' from a captured response; repeatable")
    ap.add_argument("--interval", type=float, default=1.0,
                    help="the retry interval your client currently uses")
    args = ap.parse_args()

    now = time.time()

    if args.status is not None:
        status = args.status
        headers = dict(parse_header(h) for h in args.header)
        log.info("analysing a captured %d with %d header(s)", status, len(headers))
    else:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            log.error("set GITHUB_TOKEN (a read-only token is enough), or pass "
                      "--status and --header to analyse a captured response")
            return 2
        r = requests.get(API + "/rate_limit", timeout=30, headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": UA,
        })
        status, headers = r.status_code, dict(r.headers)
        log.info("probed GET /rate_limit: %d (this endpoint does not consume "
                 "quota)", status)

    state, report = plan(status, headers, now, args.interval)
    log.info("%s: wait %.0fs from %s", state, report["wait_seconds"],
             report["source"])
    log.info("  %s", report["detail"])

    if state == "not-throttled":
        log.info("  nothing is throttled right now. Re-run with --status and "
                 "--header against a response captured during an incident to "
                 "cost your current retry policy.")
        return 0

    log.warning("  a %.1fs retry interval sends %d refused request(s) inside "
                "that window", report["retry_interval"], report["wasted_requests"])
    log.warning("  repair: sleep the whole client for %.0f second(s) before the "
                "next request, not one call.", report["wait_seconds"])
    log.warning("  repair: branch on retry-after first, then on "
                "x-ratelimit-remaining being 0 plus x-ratelimit-reset, and only "
                "then on a jittered exponential schedule such as %s",
                ", ".join("%.0fs" % s for s in report["fallback_schedule"]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
