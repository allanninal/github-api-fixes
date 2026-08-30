"""Cost a code-search scan against the bucket code search is actually billed to.

Read only. Every request is a GET. GET /rate_limit consumes no quota from any
bucket, and the optional live probe is a single search with per_page=1.

Code search is metered by resources.code_search, which is roughly 10 requests a
minute. That is a different row from resources.search and a different row again
from resources.core, and reading the wrong row is most of why this failure takes
an afternoon.
"""
import argparse
import json
import logging
import math
import os
import sys
import time

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_code_search_budget")

API = "https://api.github.com"
UA = "github-code-search-budget/1.0"

# Documented defaults, used only to fill in a row GET /rate_limit did not return.
DEFAULTS = {"code_search": 10, "search": 30, "core": 5000}

# A single search query cannot return more than this many results however many
# pages are requested, and 100 is the largest page the API will serve.
RESULT_CAP = 1000
MAX_PAGE = 100


def buckets(payload):
    """Normalise the resources table from GET /rate_limit. Pure.

    Returns {name: {"limit", "remaining", "reset", "present"}}. A row the
    deployment does not report comes back with present False and the documented
    default, because "the field is missing" and "the allowance is zero" are
    different findings and only one of them is a problem you can wait out.
    """
    resources = ((payload or {}).get("resources") or {})
    out = {}
    for name, default in DEFAULTS.items():
        raw = resources.get(name)
        if not isinstance(raw, dict):
            out[name] = {"limit": default, "remaining": None,
                         "reset": None, "present": False}
            continue
        parsed = {}
        for key in ("limit", "remaining", "reset"):
            try:
                parsed[key] = int(raw.get(key))
            except (TypeError, ValueError):
                parsed[key] = None
        out[name] = {"limit": default if parsed["limit"] is None else parsed["limit"],
                     "remaining": parsed["remaining"],
                     "reset": parsed["reset"],
                     "present": True}
    return out


def scan_cost(repos, queries_per_repo, per_minute):
    """Requests and wall-clock minutes for a scan that iterates repositories. Pure.

    The number that surprises people is minutes, not requests: at ten a minute a
    six hundred repository loop is an hour of waiting even when nothing is
    refused.
    """
    try:
        repos = max(0, int(repos))
        queries_per_repo = max(0, int(queries_per_repo))
    except (TypeError, ValueError):
        return {"requests": 0, "minutes": 0}
    per_minute = max(1, int(per_minute or 1))
    needed = repos * queries_per_repo
    return {"requests": needed,
            "minutes": math.ceil(needed / per_minute) if needed else 0}


def collapsed_cost(queries, results_per_query, per_minute,
                   page_size=MAX_PAGE, cap=RESULT_CAP):
    """Cost of the same coverage as one qualified query per concern, paged. Pure.

    Capped at `cap` because a single query cannot return more than that many
    results, so counting pages past it would promise results the API will not
    serve. `truncated` says so out loud rather than quietly under-reporting.
    """
    try:
        queries = max(0, int(queries))
        results = max(0, int(results_per_query))
    except (TypeError, ValueError):
        return {"requests": 0, "pages_per_query": 0, "minutes": 0, "truncated": False}
    page_size = max(1, min(int(page_size or MAX_PAGE), MAX_PAGE))
    reachable = min(results, cap)
    # A query with no results still costs the one request that discovers that.
    per_query = math.ceil(reachable / page_size) if reachable else 1
    needed = queries * per_query
    per_minute = max(1, int(per_minute or 1))
    return {"requests": needed, "pages_per_query": per_query,
            "minutes": math.ceil(needed / per_minute) if needed else 0,
            "truncated": results > cap}


def seconds_until(reset, now):
    """Seconds until a bucket resets, floored at zero. Pure.

    None rather than 0 when the value is unreadable: "resets right now" and "I
    could not read the reset" should not print the same.
    """
    try:
        return max(0, int(reset) - int(now))
    except (TypeError, ValueError):
        return None


def verdict(bucket, iterating, collapsed):
    """Turn the bucket state and the two costings into a finding. Pure."""
    limit = bucket.get("limit") or DEFAULTS["code_search"]
    remaining = bucket.get("remaining")
    note = "" if bucket.get("present") else (
        " (GET /rate_limit did not report a code_search row, so this uses the "
        "documented default of %d a minute)" % limit)

    if remaining == 0:
        return ("exhausted",
                "the code_search bucket is empty. This is not the core quota, "
                "which is why it can read as thousands remaining at the same "
                "time. It refills on its own minute-long clock.%s" % note)
    if not iterating.get("requests"):
        return ("no-scan", "no scan described, so nothing to cost%s" % note)

    ratio = iterating["requests"] / max(1, collapsed.get("requests") or 1)
    if ratio >= 4:
        return ("per-repo-scan",
                "%d request(s) is %d minute(s) at %d a minute; the same coverage "
                "as %d qualified quer(y/ies) is %d request(s) and %d minute(s). "
                "The loop is the cost, not the caching.%s"
                % (iterating["requests"], iterating["minutes"], limit,
                   max(1, collapsed.get("requests", 0) // max(1, collapsed.get("pages_per_query", 1))),
                   collapsed.get("requests", 0), collapsed.get("minutes", 0), note))
    if iterating["minutes"] > 1:
        return ("over-budget",
                "%d request(s) at %d a minute is %d minute(s) of wall clock even "
                "if nothing is refused.%s"
                % (iterating["requests"], limit, iterating["minutes"], note))
    return ("clear",
            "%d request(s) fits inside one minute of a %d a minute allowance.%s"
            % (iterating["requests"], limit, note))


def get(session, path, **kwargs):
    """One GET. Returns (status, json-or-None, headers)."""
    url = API + path if path.startswith("/") else path
    r = session.get(url, timeout=30, **kwargs)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body, dict(r.headers)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repos", type=int, default=0,
                    help="repositories the current scan iterates over")
    ap.add_argument("--queries", type=int, default=1,
                    help="code-search queries issued per repository")
    ap.add_argument("--results", type=int, default=200,
                    help="results you expect one qualified query to match")
    ap.add_argument("--probe-query",
                    help="optional q= value; issues one search with per_page=1 "
                         "to read x-ratelimit-resource on a live response")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN. Code search refuses unauthenticated "
                  "callers outright, so there is no anonymous fallback here")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    status, payload, _ = get(session, "/rate_limit")
    if status != 200:
        log.error("GET /rate_limit returned %d; cannot read the buckets", status)
        return 2

    table = buckets(payload)
    for name in ("core", "search", "code_search"):
        row = table[name]
        wait = seconds_until(row["reset"], time.time())
        log.info("%-12s limit %-5s remaining %-5s reset in %s",
                 name, row["limit"],
                 "?" if row["remaining"] is None else row["remaining"],
                 "unknown" if wait is None else "%ds" % wait)
        if not row["present"]:
            log.warning("  %s was not in the resources table; showing the "
                        "documented default", name)

    if args.probe_query:
        status, _, headers = get(session, "/search/code",
                                 params={"q": args.probe_query, "per_page": 1})
        lowered = {k.lower(): v for k, v in headers.items()}
        log.info("probe: /search/code returned %d, billed to %s",
                 status, lowered.get("x-ratelimit-resource", "an unnamed bucket"))
        if status == 403:
            log.warning("  a 403 here with core headroom left is this bucket, "
                        "not the hourly quota and not your token scopes")

    code = table["code_search"]
    iterating = scan_cost(args.repos, args.queries, code["limit"])
    collapsed = collapsed_cost(max(1, args.queries), args.results, code["limit"])
    state, detail = verdict(code, iterating, collapsed)
    log.info("%s: %s", state, detail)

    if collapsed["truncated"]:
        log.warning("one query cannot return more than %d results, so the "
                    "collapsed costing counts %d page(s) and stops. Narrow by "
                    "path, extension or date rather than paging further.",
                    RESULT_CAP, collapsed["pages_per_query"])

    if state in ("per-repo-scan", "over-budget", "exhausted"):
        log.info("repair: one qualified query instead of one per repository, "
                 "for example q=YOURTERM+org:YOURORG with per_page=100, and "
                 "follow the Link header.")
        log.info("repair: for an exhaustive audit, shallow-clone and grep "
                 "locally. The search index is capped, ranked and metered; a "
                 "clone is none of those.")

    print(json.dumps({"buckets": table, "iterating": iterating,
                      "collapsed": collapsed, "state": state}, indent=2))
    return 1 if state in ("per-repo-scan", "over-budget", "exhausted") else 0


if __name__ == "__main__":
    sys.exit(main())
