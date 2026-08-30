"""Say whether a GitHub search is being answered in part and nobody noticed.

Read only. One GET per round against /search/*, three rounds by default, with a
pause between them. Nothing is written.

Search runs against a server-side timeout. When a query outruns it GitHub does
not fail: it returns what it found with the top-level incomplete_results boolean
set to true. Status 200, valid JSON, fewer items, no error anywhere. A client
that reads items and ignores the flag treats a partial answer as a complete one,
and the count quietly moves between runs.

This is not the 1,000-result ceiling. That limit is deterministic, announces
itself with a 422 when you page past it, and applies to queries with more than a
thousand matches. This flag is non-deterministic, arrives on the first page, and
fires on queries nowhere near the cap. total_count is read here purely so the
ceiling can be ruled out by name.

Search has its own rate bucket, separate from and much tighter than core, so
this check is deliberately tiny and refuses a plan that would not fit in it.

Environment:

    GITHUB_TOKEN    any token with read access; search needs authentication to
                    get the larger of the two search buckets
"""
import argparse
import json
import logging
import os
import re
import sys
import time

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_search_incomplete")

API = "https://api.github.com"
UA = "github-search-incomplete/1.0"

# The other Search limit, read only so it can be excluded as an explanation.
RESULT_CAP = 1000
# Authenticated search requests per minute. The check is sized against this.
SEARCH_BUCKET = 30

QUALIFIER = re.compile(r"(?:^|\s)-?([A-Za-z_]+):\S")

SCOPES = ("repo", "org", "user")
RANGES = ("created", "updated", "merged", "closed")


def flagged(body):
    """Whether this response says it is partial. Pure."""
    return bool(isinstance(body, dict) and body.get("incomplete_results") is True)


def total_of(body):
    """The reported match count, or None. Pure."""
    if not isinstance(body, dict):
        return None
    try:
        return int(body.get("total_count"))
    except (TypeError, ValueError):
        return None


def item_count(body):
    """How many items were actually delivered. Pure."""
    if not isinstance(body, dict):
        return 0
    items = body.get("items")
    return len(items) if isinstance(items, list) else 0


def cacheable(body):
    """Whether this response may be stored. Pure.

    The rule the whole note comes down to: a partial answer written to a cache
    without a note of its partiality becomes the permanent truth.
    """
    return isinstance(body, dict) and not flagged(body)


def above_result_cap(total):
    """Whether the ceiling could also be in play here. Pure."""
    try:
        return int(total) > RESULT_CAP
    except (TypeError, ValueError):
        return False


def qualifiers(query):
    """The qualifier names used in a search query. Pure."""
    return set(QUALIFIER.findall(" " + str(query or "")))


def narrowing(query):
    """Which narrowing devices the query is not already using. Pure."""
    have = qualifiers(query)
    out = []
    if not have & set(SCOPES):
        out.append("repo: or org:")
    if not have & set(RANGES):
        out.append("created: or updated: date range")
    if "language" not in have:
        out.append("language:")
    return out


def observe(body):
    """The three fields worth keeping from one response. Pure."""
    return {"incomplete": flagged(body),
            "total": total_of(body),
            "items": item_count(body)}


def summarise(observations):
    """Counts over the sequence of rounds. Pure."""
    obs = list(observations or [])
    return {"rounds": len(obs),
            "flagged": sum(1 for o in obs if o.get("incomplete")),
            "item_counts": [o.get("items") for o in obs],
            "totals": [o.get("total") for o in obs]}


def counts_stable(observations):
    """Whether identical queries returned identical item counts. Pure."""
    counts = [o.get("items") for o in (observations or [])]
    return len(set(counts)) <= 1


def max_total(observations):
    """The largest reported match count across the rounds, or None. Pure."""
    totals = [o.get("total") for o in (observations or [])
              if o.get("total") is not None]
    return max(totals) if totals else None


def verdict(observations):
    """Classify the sequence. Pure. Returns (state, detail).

    Built from the sequence rather than from any one response, because a single
    flagged round cannot distinguish an unlucky query from a hopeless one, and
    those two have opposite repairs.
    """
    s = summarise(observations)
    if not s["rounds"]:
        return ("no-observations", "no round completed, so there is nothing to judge.")
    top = max_total(observations)
    ceiling = (" total_count is %s, well inside the %d-result ceiling, so the "
               "ceiling is not the explanation." % (top, RESULT_CAP)
               if top is not None and not above_result_cap(top) else "")
    if s["flagged"] and top is not None and above_result_cap(top):
        return ("timed-out-and-capped",
                "%d of %d round(s) came back partial and total_count is %s, "
                "which is also above the %d-result ceiling. These are two "
                "separate problems that look alike from outside and need "
                "repairing separately."
                % (s["flagged"], s["rounds"], top, RESULT_CAP))
    if s["flagged"] == s["rounds"]:
        return ("timed-out-always",
                "every one of %d round(s) came back partial, so this query does "
                "not finish inside the search timeout. No retry policy will fix "
                "that.%s" % (s["rounds"], ceiling))
    if s["flagged"]:
        return ("timed-out-intermittent",
                "%d of %d round(s) came back partial, so the query sometimes "
                "finishes and sometimes does not. A flagged response is a retry, "
                "not a result.%s" % (s["flagged"], s["rounds"], ceiling))
    if not counts_stable(observations):
        return ("unstable-counts",
                "no round was flagged, but identical queries returned %s "
                "item(s) across the rounds. Something is truncating or "
                "reordering underneath you, and the answer should be treated "
                "the same way as a flagged one."
                % " and ".join(str(c) for c in sorted(set(s["item_counts"]))))
    return ("complete",
            "%d of %d round(s) were unflagged and the item count did not move."
            % (s["rounds"], s["rounds"]))


def retry_or_narrow(observations):
    """What actually helps: retry, narrow, or nothing. Pure."""
    state = verdict(observations)[0]
    if state in ("timed-out-always", "timed-out-and-capped"):
        return "narrow"
    if state in ("timed-out-intermittent", "unstable-counts"):
        return "retry"
    return "nothing"


def repair(state, query=""):
    """The sentence a reader has to act on. Pure."""
    missing = ", ".join(narrowing(query)) or "nothing obvious"
    if state == "timed-out-always":
        return ("narrow the query until it finishes: add %s. Retrying will "
                "spend your search bucket on the same partial answer." % missing)
    if state == "timed-out-and-capped":
        return ("narrow the query until it finishes and until each slice reports "
                "under %d matches: add %s, then union the slices yourself."
                % (RESULT_CAP, missing))
    if state == "timed-out-intermittent":
        return ("treat a flagged response as a retry, never as a result, and "
                "never cache it. If the flag keeps coming back, add %s." % missing)
    if state == "unstable-counts":
        return ("treat this the same as a flagged response: do not cache it and "
                "do not diff against it. A moving count with no flag is still a "
                "moving count.")
    return "nothing."


def read_cost(queries, rounds):
    """Search requests this run will spend. Pure."""
    try:
        return max(0, len(queries or [])) * max(0, int(rounds))
    except (TypeError, ValueError):
        return 0


def within_search_bucket(cost):
    """Whether a plan of this size fits the per-minute search allowance. Pure."""
    try:
        return 0 < int(cost) <= SEARCH_BUCKET
    except (TypeError, ValueError):
        return False


def search(session, kind, query, per_page):
    """One search GET. Returns the decoded body or None."""
    r = session.get("%s/search/%s" % (API, kind),
                    params={"q": query, "per_page": per_page}, timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed "
                         "or revoked")
    if r.status_code == 403:
        raise SystemExit("403 from search. The search bucket is separate from "
                         "core and much tighter; wait for the window to reset")
    if r.status_code == 422:
        raise SystemExit("422 from search: the query is invalid, or you paged "
                         "past the %d-result ceiling" % RESULT_CAP)
    if r.status_code != 200:
        log.info("search returned %d", r.status_code)
        return None
    try:
        return r.json()
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--query", required=True, help="the search query, as sent")
    ap.add_argument("--kind", default="issues",
                    help="issues, repositories, code, users, commits")
    ap.add_argument("--rounds", type=int, default=3,
                    help="how many times to run the same query")
    ap.add_argument("--pause", type=float, default=2.0,
                    help="seconds between rounds")
    ap.add_argument("--per-page", type=int, default=100,
                    help="page size for each round")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN. Unauthenticated search gets a much smaller "
                  "bucket and this check would exhaust it")
        return 2

    cost = read_cost([args.query], args.rounds)
    if not within_search_bucket(cost):
        log.error("%d request(s) does not fit the %d per minute search bucket; "
                  "lower --rounds", cost, SEARCH_BUCKET)
        return 2
    log.info("read cost: %d search request(s) of the %d per minute search bucket",
             cost, SEARCH_BUCKET)

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })

    observations = []
    for n in range(args.rounds):
        if n:
            time.sleep(args.pause)
        body = search(session, args.kind, args.query, args.per_page)
        if body is None:
            continue
        o = observe(body)
        observations.append(o)
        log.info("round %d: %d item(s), total_count %s, incomplete_results=%s",
                 n + 1, o["items"], o["total"], str(o["incomplete"]).lower())
        if not cacheable(body):
            log.info("round %d must not be cached or diffed against", n + 1)

    state, detail = verdict(observations)
    log.info("%s: %s", state, detail)
    missing = narrowing(args.query)
    if missing:
        log.info("missing from the query: %s", ", ".join(missing))
    log.info("what helps: %s", retry_or_narrow(observations))
    log.info("repair: %s", repair(state, args.query))

    print(json.dumps({"query": args.query,
                      "requests_spent": len(observations),
                      "summary": summarise(observations),
                      "counts_stable": counts_stable(observations),
                      "total_above_cap": above_result_cap(max_total(observations)),
                      "qualifiers_used": sorted(qualifiers(args.query)),
                      "narrowing_available": missing,
                      "action": retry_or_narrow(observations),
                      "state": state,
                      "detail": detail}, indent=2, default=str))
    return 1 if state not in ("complete", "no-observations") else 0


if __name__ == "__main__":
    sys.exit(main())
