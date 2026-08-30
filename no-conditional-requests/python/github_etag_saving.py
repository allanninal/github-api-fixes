"""Measure what conditional requests would save against the GitHub rate limit.

Read only. Two GETs against one endpoint: the second sends If-None-Match with the
ETag the first returned. A 304 Not Modified does not count against the primary
rate limit, and x-ratelimit-used on both responses proves it rather than
asserting it.
"""
import argparse
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_etag_saving")

API = "https://api.github.com"
UA = "github-etag-saving/1.0"

DEFAULT_LIMIT = 5000


def measure(first, second):
    """Compare a plain response with the conditional one that followed. Pure.

    Each argument is {"status": int, "etag": str|None, "used": int|None}. Returns
    (state, report). The states are deliberately separate because they have
    nothing in common: an endpoint that sends no ETag cannot be cached at all, an
    endpoint that answers 200 to a conditional request is being interfered with,
    and a 304 that still increments used would mean the documented discount did
    not apply.
    """
    etag = (first or {}).get("etag")
    before = (first or {}).get("used")
    after = (second or {}).get("used")
    status = (second or {}).get("status")

    try:
        delta = int(after) - int(before)
    except (TypeError, ValueError):
        delta = None

    report = {"etag": etag, "used_before": before, "used_after": after,
              "cost_of_unchanged_poll": delta,
              "first_status": (first or {}).get("status"), "second_status": status}

    if not etag:
        return ("no-etag", report)
    if status != 304:
        return ("not-honoured", report)
    if delta is None:
        return ("unmeasured", report)
    if delta > 0:
        return ("billed", report)
    return ("free", report)


def project(poll_seconds, endpoints, limit=DEFAULT_LIMIT, unchanged_fraction=1.0):
    """Price a polling schedule with and without conditional requests. Pure.

    unchanged_fraction is how much of what you poll is typically unchanged. At
    1.0 every poll is a 304 and costs nothing; at 0.0 nothing is cacheable and
    conditional requests save nothing, which is the honest end of the range.
    """
    poll_seconds = max(1.0, float(poll_seconds))
    endpoints = max(1, int(endpoints))
    limit = max(1, int(limit))
    fraction = min(1.0, max(0.0, float(unchanged_fraction)))

    without = (3600.0 / poll_seconds) * endpoints
    with_etags = without * (1.0 - fraction)
    return {"per_hour_without": round(without, 1),
            "per_hour_with": round(with_etags, 1),
            "saved_per_hour": round(without - with_etags, 1),
            "percent_without": round(100.0 * without / limit, 1),
            "percent_with": round(100.0 * with_etags / limit, 1),
            "limit": limit}


def verdict(state, projection):
    """Turn the measurement and the projection into one line. Pure."""
    saved = (projection or {}).get("saved_per_hour", 0)
    percent = (projection or {}).get("percent_without", 0)

    if state == "no-etag":
        return ("unavailable",
                "the response carried no etag, so this endpoint cannot be polled "
                "conditionally. Check last-modified and use if-modified-since "
                "where it is present.")
    if state == "not-honoured":
        return ("ignored",
                "the conditional request came back 200 rather than 304. Either "
                "the resource genuinely changed between the two calls, or "
                "something between this client and GitHub is dropping the "
                "If-None-Match header, which silently reinstates the full cost.")
    if state == "billed":
        return ("billed",
                "the 304 arrived and x-ratelimit-used still moved, which is not "
                "how conditional requests are documented to behave. Re-run "
                "before acting on it: another process sharing this token spends "
                "the same counter.")
    if state == "unmeasured":
        return ("unmeasured",
                "the 304 arrived but x-ratelimit-used was missing from one of "
                "the responses, so the saving is real and its size is not "
                "measured here.")
    return ("saving" if percent < 25 else "large-saving",
            "the 304 cost 0 request(s). At this poll rate that is %.0f request(s) "
            "an hour, %.1f%% of the quota, currently spent on data that did not "
            "change." % (saved, percent))


def read(response):
    """Reduce a response to the three fields the measurement needs."""
    headers = {k.lower(): v for k, v in response.headers.items()}
    try:
        used = int(headers.get("x-ratelimit-used"))
    except (TypeError, ValueError):
        used = None
    return {"status": response.status_code, "etag": headers.get("etag"),
            "used": used, "last_modified": headers.get("last-modified"),
            "limit": headers.get("x-ratelimit-limit")}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--path", default="/issues",
                    help="path under the repository to probe, e.g. /issues")
    ap.add_argument("--poll-seconds", type=float, default=60.0,
                    help="how often your integration polls this endpoint")
    ap.add_argument("--endpoints", type=int, default=1,
                    help="how many endpoints are polled on that schedule")
    ap.add_argument("--unchanged", type=float, default=1.0,
                    help="fraction of polls that find nothing changed (0 to 1)")
    args = ap.parse_args()

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

    url = "%s/repos/%s/%s%s" % (API, owner, name, args.path)
    log.info("probing %s twice: once plain, once with If-None-Match", url)

    plain = session.get(url, timeout=30)
    if plain.status_code == 401:
        log.error("401 from GitHub: GITHUB_TOKEN is missing, expired or malformed")
        return 2
    if plain.status_code in (403, 404):
        log.error("%d from %s: this token cannot read that endpoint. GitHub "
                  "answers 404 rather than 403 when a token cannot see a "
                  "resource at all.", plain.status_code, url)
        return 2
    first = read(plain)
    log.info("  plain:       %d, etag %s, x-ratelimit-used %s",
             first["status"], first["etag"], first["used"])

    second = first
    if first["etag"]:
        conditional = session.get(url, timeout=30,
                                  headers={"If-None-Match": first["etag"]})
        second = read(conditional)
        log.info("  conditional: %d, x-ratelimit-used %s",
                 second["status"], second["used"])
    elif first["last_modified"]:
        log.warning("  no etag, but last-modified is %s: use if-modified-since "
                    "on this endpoint instead", first["last_modified"])

    state, report = measure(first, second)
    limit = DEFAULT_LIMIT
    try:
        limit = int(first["limit"])
    except (TypeError, ValueError):
        pass

    projection = project(args.poll_seconds, args.endpoints, limit, args.unchanged)
    level, detail = verdict(state, projection)
    log.info("%s: %s", level, detail)
    log.info("  %.0f request(s)/hour now (%.1f%% of %d), %.0f/hour with "
             "conditional requests (%.1f%%)",
             projection["per_hour_without"], projection["percent_without"],
             projection["limit"], projection["per_hour_with"],
             projection["percent_with"])

    if level in ("saving", "large-saving"):
        log.info("  repair: store %s against this exact URL and credential, send "
                 "it back as If-None-Match, and treat 304 as 'keep what you "
                 "have' rather than as an error.", report["etag"])
        log.info("  repair: keep per_page, sort and Accept stable, and key the "
                 "cache by token: an ETag is scoped to the credential that "
                 "fetched it, so a rotation invalidates every entry at once.")
    return 0 if level in ("saving", "large-saving", "unavailable") else 1


if __name__ == "__main__":
    sys.exit(main())
