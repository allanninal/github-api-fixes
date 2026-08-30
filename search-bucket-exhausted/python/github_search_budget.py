"""Budget a search workload against the search bucket, not the core one.

Read only. GET /rate_limit is free and reports every bucket; the optional probe
issues one real search, which is a GET and costs one search call.

Search is billed to resources.search, which allows 30 requests a minute
authenticated over a 60 second window. Core allows 5,000 an hour, which is
about 83 a minute. Comparing 5,000 against 30 is what makes people think search
is the generous one; comparing 83 against 30 is what makes them stop.
"""
import argparse
import json
import logging
import os
import sys
import time

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_search_budget")

API = "https://api.github.com"
UA = "github-search-budget/1.0"

# A search query is capped at 256 characters and five boolean operators.
MAX_QUERY = 256
MAX_OPERATORS = 5

# The rate-limit document reports limit, used and reset for every bucket but
# never the length of the window, and the windows are not all the same. Without
# this table a per-minute comparison is impossible, which is the comparison the
# whole note turns on.
WINDOWS = {
    "core": 3600, "graphql": 3600, "integration_manifest": 3600,
    "code_scanning_upload": 3600, "actions_runner_registration": 3600,
    "scim": 3600, "dependency_sbom": 3600, "audit_log": 3600,
    "search": 60, "code_search": 60, "source_import": 60,
    "dependency_snapshots": 60,
}


def bucket_pressure(resources, now):
    """Normalise every bucket to requests per minute. Pure.

    A bucket whose window this table does not know is still reported, with
    per_minute left as None rather than guessed. An invented window would
    produce a confident number that is wrong, and the point of the function is
    to make two numbers comparable.
    """
    out = {}
    for name, b in sorted((resources or {}).items()):
        try:
            limit = int(b.get("limit"))
            used = int(b.get("used", 0))
            reset = float(b.get("reset", 0))
        except (AttributeError, TypeError, ValueError):
            continue
        window = WINDOWS.get(name)
        remaining = b.get("remaining")
        if not isinstance(remaining, int):
            remaining = max(0, limit - used)
        out[name] = {
            "limit": limit, "used": used, "remaining": remaining,
            "window": window,
            "per_minute": round(limit / (window / 60.0), 1) if window else None,
            "refills_in": max(0, round(reset - float(now))),
        }
    return out


def plan_loop(items, per_minute):
    """Cost a one-call-per-item loop against a per-minute allowance. Pure.

    Calls past the allowance in a given minute are refused, not queued, which
    is the difference between a slow job and a failing one.
    """
    try:
        items = max(0, int(items))
    except (TypeError, ValueError):
        items = 0
    try:
        rate = float(per_minute)
    except (TypeError, ValueError):
        rate = 0.0

    if rate <= 0:
        return {"calls": items, "minutes": None, "refused_in_first_minute": None}
    return {"calls": items,
            "minutes": round(items / rate, 1),
            "refused_in_first_minute": max(0, items - int(rate))}


def pack_repo_queries(repos, base="", max_len=MAX_QUERY, max_operators=MAX_OPERATORS):
    """Pack repo: qualifiers into as few queries as the length limit allows. Pure.

    Multiple repo: qualifiers are combined as alternatives and do not spend
    boolean operators, so the binding constraint is the 256 character budget.
    Greedy is optimal enough here: the qualifiers are all about the same length,
    so there is nothing for a cleverer pack to recover.

    Returns {"queries", "too_long", "operators"}. too_long holds any single
    repository that cannot fit even on its own, which is a real if rare case
    for a long org and repository name under a long base query.
    """
    base = (base or "").strip()
    operators = sum(1 for token in base.split() if token in ("AND", "OR", "NOT"))

    queries, too_long = [], []
    current = ""
    for repo in repos or []:
        name = str(repo).strip()
        if not name:
            continue
        qualifier = "repo:" + name
        if len(base) + 1 + len(qualifier) > max_len:
            too_long.append(name)
            continue
        candidate = (current + " " + qualifier).strip() if current else qualifier
        if len(base) + (1 if base else 0) + len(candidate) <= max_len:
            current = candidate
        else:
            queries.append((base + " " + current).strip() if base else current)
            current = qualifier
    if current:
        queries.append((base + " " + current).strip() if base else current)

    return {"queries": queries, "too_long": too_long, "operators": operators,
            "over_operator_limit": operators > max_operators}


def verdict(search, core, plan=None, packed=None):
    """Turn the buckets and the plan into one finding. Pure."""
    if not search:
        return ("no-search-bucket",
                "the rate-limit document did not include a search bucket, so "
                "there is nothing to budget against")

    core_rate = (core or {}).get("per_minute")
    comparison = ("" if core_rate is None else
                  " Core allows %.0f a minute over its hour, so search is the "
                  "tighter of the two despite the larger-looking number."
                  % core_rate)

    if search["remaining"] <= 0:
        return ("exhausted",
                "search is empty and refills in %d second(s). Core still has "
                "%s of %s, which is why every non-search call kept working: "
                "they are different buckets."
                % (search["refills_in"], (core or {}).get("remaining", "?"),
                   (core or {}).get("limit", "?")))

    if plan and plan.get("refused_in_first_minute"):
        packing = ""
        if packed and packed.get("queries"):
            packing = (" Packed into repo: qualifiers the same work is %d "
                       "quer%s." % (len(packed["queries"]),
                                    "y" if len(packed["queries"]) == 1 else "ies"))
        return ("over-budget",
                "%d searches at %s a minute needs %s minute(s), and %d of them "
                "are refused inside the first minute rather than queued.%s%s"
                % (plan["calls"], search["per_minute"], plan["minutes"],
                   plan["refused_in_first_minute"], packing, comparison))

    if search["used"] >= search["limit"] * 0.8:
        return ("tight",
                "%d of %d spent in the current 60 second window, refilling in "
                "%d second(s).%s"
                % (search["used"], search["limit"], search["refills_in"],
                   comparison))

    if plan and plan.get("calls"):
        return ("clear",
                "%d search(es) at %s a minute fits in %s minute(s) with nothing "
                "refused.%s" % (plan["calls"], search["per_minute"],
                                plan["minutes"], comparison))

    return ("clear",
            "%d of %d left in this window.%s"
            % (search["remaining"], search["limit"], comparison))


def rate_limit(session):
    r = session.get(API + "/rate_limit", timeout=30)
    if r.status_code != 200:
        log.error("GET /rate_limit returned %d: %s", r.status_code, r.text[:200])
        return None
    return r.json().get("resources", {})


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repos", default="",
                    help="comma separated owner/name list the loop searches")
    ap.add_argument("--repos-file", default=None,
                    help="file with one owner/name per line (read only)")
    ap.add_argument("--base", default="is:issue is:open",
                    help="the query your loop runs in each repository")
    ap.add_argument("--probe", default=None, metavar="QUERY",
                    help="run one real search to show which bucket it bills to")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    before = rate_limit(session)
    if before is None:
        return 2

    pressure = bucket_pressure(before, time.time())
    for name, b in pressure.items():
        log.info("%-28s %5d / %-6d %s",
                 name, b["used"], b["limit"],
                 "%.0f a minute" % b["per_minute"] if b["per_minute"]
                 else "window not in this table")

    repos = [r.strip() for r in args.repos.split(",") if r.strip()]
    if args.repos_file:
        with open(args.repos_file, encoding="utf-8") as fh:
            repos.extend(line.strip() for line in fh if line.strip())

    search = pressure.get("search")
    plan = plan_loop(len(repos), (search or {}).get("per_minute")) if repos else None
    packed = pack_repo_queries(repos, args.base) if repos else None

    if args.probe:
        r = session.get(API + "/search/issues", params={"q": args.probe, "per_page": 1},
                        timeout=30)
        billed = {k.lower(): v for k, v in r.headers.items()}.get("x-ratelimit-resource")
        after = bucket_pressure(rate_limit(session) or {}, time.time())
        log.info("probe returned %d, billed to the %s bucket", r.status_code, billed)
        log.info("search.used %d -> %d, core.used %d -> %d",
                 pressure["search"]["used"], after["search"]["used"],
                 pressure["core"]["used"], after["core"]["used"])

    state, detail = verdict(search, pressure.get("core"), plan, packed)
    log.info("%s: %s", state, detail)

    if packed and packed["queries"] and state != "clear":
        log.info("repair: run these %d quer%s instead of one per repository, "
                 "and filter the combined results client side:",
                 len(packed["queries"]),
                 "y" if len(packed["queries"]) == 1 else "ies")
        for q in packed["queries"][:10]:
            log.info("  %s", q)
        if len(packed["queries"]) > 10:
            log.info("  ... and %d more", len(packed["queries"]) - 10)
    if packed and packed["too_long"]:
        log.warning("%d repositor(y/ies) cannot fit in a %d character query "
                    "beside this base query and still need their own call: %s",
                    len(packed["too_long"]), MAX_QUERY,
                    ", ".join(packed["too_long"][:5]))
    if packed and packed["over_operator_limit"]:
        log.warning("the base query already uses %d boolean operators and the "
                    "limit is %d", packed["operators"], MAX_OPERATORS)
    if state != "clear":
        log.info("repair: where a list endpoint can answer the same question, "
                 "use it instead. Issues, pull requests and commits all have "
                 "list endpoints billed to core rather than to search.")
        log.info("repair: cache search results by query string. The allowance "
                 "counts requests, so a repeated query is pure waste.")

    print(json.dumps({"state": state, "search": search,
                      "core": pressure.get("core"), "plan": plan,
                      "queries": (packed or {}).get("queries", [])}, indent=2))
    return 1 if state in ("exhausted", "over-budget") else 0


if __name__ == "__main__":
    sys.exit(main())
