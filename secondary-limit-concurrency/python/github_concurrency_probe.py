"""Measure the concurrency a client actually reaches, and classify any throttling.

Read only. Every request is a GET, and the default probe endpoint is
GET /rate_limit, which does not count against the primary rate limit.

There is no API for secondary-limit headroom: no x-ratelimit-* field tracks one
and GET /rate_limit covers primary quota only. So this script does not predict a
secondary limit. It measures the fan-out this client reaches and reports
correctly if one fires.
"""
import argparse
import concurrent.futures
import json
import logging
import os
import sys
import time

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_concurrency_probe")

API = "https://api.github.com"
UA = "github-concurrency-probe/1.0"

# Documented ceiling on requests in flight at once, across REST and GraphQL.
CONCURRENCY_CEILING = 100

# The wording has changed over the years, so match on the stable part of both
# the current phrasing and the one that predates it.
SECONDARY_MARKERS = ("secondary rate limit", "abuse detection")


def classify(status, body, headers):
    """Sort one response into primary, secondary, permission or fine. Pure.

    Returns (state, detail). The distinguishing field is x-ratelimit-remaining on
    the refused response itself: a primary exhaustion reports 0 there because that
    is what exhausted means, while a secondary limit leaves the primary bucket
    untouched. So "403 with headroom left" is the signature, and it still holds
    when the message wording is one this code has never seen.
    """
    lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
    text = str(body or "").lower()
    try:
        remaining = int(lowered.get("x-ratelimit-remaining"))
    except (TypeError, ValueError):
        remaining = None

    try:
        status = int(status)
    except (TypeError, ValueError):
        status = 0

    if 200 <= status < 400:
        seen = "unknown" if remaining is None else str(remaining)
        return ("ok", "%d, primary bucket reports %s left" % (status, seen))
    if status not in (403, 429):
        return ("other", "%d is not a throttle at all" % status)

    if any(marker in text for marker in SECONDARY_MARKERS):
        return ("secondary",
                "%d and the body names a secondary rate limit. The hourly quota "
                "is not involved: it still reports %s remaining."
                % (status, "an unknown number" if remaining is None else remaining))
    if remaining == 0:
        return ("primary",
                "%d with x-ratelimit-remaining at 0. This is the hourly quota, "
                "not a secondary limit, and it clears at x-ratelimit-reset."
                % status)
    if remaining is not None and remaining > 0:
        return ("secondary-suspected",
                "%d while %d request(s) remain in the primary bucket. The body "
                "does not say secondary, but a refusal with headroom left did "
                "not come from the bucket these headers describe."
                % (status, remaining))
    return ("forbidden",
            "%d with no rate-limit headers to read. Treat this as permissions "
            "until something proves otherwise." % status)


def peak_overlap(spans):
    """Peak number of requests in flight at once, from (start, end) pairs. Pure.

    A sweep rather than a max of the pool size, because the pool size is a
    ceiling and this is the number that was actually reached. Twenty workers
    against a 40 ms endpoint rarely overlap; six against a four-second one
    always do.
    """
    events = []
    for span in spans or []:
        start, end = float(span[0]), float(span[1])
        if end < start:
            start, end = end, start
        events.append((start, 1))
        events.append((end, -1))
    # A request that ended at the exact instant another began was never beside
    # it, so ends are ordered before starts at an equal timestamp.
    events.sort(key=lambda e: (e[0], e[1]))
    peak = current = 0
    for _, delta in events:
        current += delta
        if current > peak:
            peak = current
    return peak


def verdict(peak, states, ceiling=CONCURRENCY_CEILING):
    """Turn a peak overlap and a list of response states into a finding. Pure.

    "clear" deliberately does not say the client is safe. Nothing can say that:
    the limit has no headroom API, so a probe that did not trip it has shown
    only that this run, at this moment, did not trip it.
    """
    throttled = [s for s in (states or []) if s in ("secondary", "secondary-suspected")]
    if throttled:
        return ("tripped",
                "%d of %d response(s) were refused with the primary bucket still "
                "healthy. Peak overlap was %d. Bound the pool and honour "
                "retry-after." % (len(throttled), len(states or []), peak))
    if peak >= ceiling:
        return ("over-ceiling",
                "peak overlap %d at or above the documented ceiling of %d. This "
                "run happened not to be refused; a slower endpoint or a busier "
                "moment will be." % (peak, ceiling))
    if peak >= ceiling * 0.8:
        return ("near-ceiling",
                "peak overlap %d against a ceiling of %d. One more worker or one "
                "slow response is the difference." % (peak, ceiling))
    return ("clear",
            "peak overlap %d of a %d ceiling, nothing throttled. This proves the "
            "run was fine, not that the client is: secondary limits have no "
            "headroom API to check against." % (peak, ceiling))


def probe(session, url, index):
    """One timed GET. Returns a record; never raises, because a failed request
    is data here rather than an error."""
    start = time.monotonic()
    try:
        r = session.get(url, timeout=30)
        end = time.monotonic()
        return {"i": index, "start": start, "end": end, "status": r.status_code,
                "body": r.text[:400], "headers": dict(r.headers)}
    except requests.RequestException as exc:
        return {"i": index, "start": start, "end": time.monotonic(), "status": 0,
                "body": str(exc), "headers": {}}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--endpoint", default="/rate_limit",
                    help="path to probe (default /rate_limit, which is free)")
    ap.add_argument("--requests", type=int, default=12,
                    help="how many GETs to issue in total")
    ap.add_argument("--concurrency", type=int, default=6,
                    help="worker pool size; the ceiling, not the achieved peak")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    workers = max(1, min(args.concurrency, CONCURRENCY_CEILING))
    if workers != args.concurrency:
        log.warning("clamping concurrency to %d: going past the documented "
                    "ceiling on purpose spends a shared quota to learn nothing "
                    "new", workers)

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    url = API + args.endpoint if args.endpoint.startswith("/") else args.endpoint
    log.info("probing %s: %d request(s), pool of %d", url, args.requests, workers)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda i: probe(session, url, i),
                                range(max(1, args.requests))))

    states = []
    for r in sorted(results, key=lambda r: r["i"]):
        state, detail = classify(r["status"], r["body"], r["headers"])
        states.append(state)
        if state in ("ok", "other"):
            log.debug("request %d: %s %s", r["i"], state, detail)
        else:
            log.warning("request %d: %-20s %s", r["i"], state, detail)
            retry_after = {k.lower(): v for k, v in r["headers"].items()}.get("retry-after")
            if retry_after:
                log.warning("  retry-after: %s second(s). Pause the whole pool "
                            "for that long, not just this request.", retry_after)

    peak = peak_overlap([(r["start"], r["end"]) for r in results])
    state, detail = verdict(peak, states)
    log.info("%s: %s", state, detail)

    if state != "clear":
        log.info("repair: replace the fan-out with a bounded pool. Python: "
                 "ThreadPoolExecutor(max_workers=6). Node: a queue of 6 rather "
                 "than Promise.all over the whole input list.")
        log.info("repair: on a throttled response sleep retry-after seconds "
                 "before resuming any worker, and where the header is absent "
                 "wait 60 seconds and then back off exponentially.")

    print(json.dumps({"peak_overlap": peak, "ceiling": CONCURRENCY_CEILING,
                      "requests": len(results), "state": state,
                      "states": states}, indent=2))
    return 1 if state in ("tripped", "over-ceiling") else 0


if __name__ == "__main__":
    sys.exit(main())
