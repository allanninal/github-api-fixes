"""Report search queries whose results cannot be paged through in full.

Read only. GET requests and nothing else: a token with read access is enough.
The repair is printed, never performed.

The cap is a property of the query, so this is one of the few checks here that
gives a complete answer: total_count above 1,000 means results exist that no
client, correct or otherwise, can reach.
"""
import argparse
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_search_cap_audit")

API = "https://api.github.com"

CAP = 1000
NEAR = 900
MAX_PER_PAGE = 100


def last_reachable_page(per_page=MAX_PER_PAGE):
    """The highest page number that lies entirely inside the 1,000-result cap.

    Pure. The request that straddles the boundary is the one that returns 422,
    so this is the page after which a walk stops working: 10 at per_page=100,
    33 at per_page=30.
    """
    size = min(max(int(per_page or 30), 1), MAX_PER_PAGE)
    return CAP // size


def reach(total_count, per_page=MAX_PER_PAGE):
    """Classify one query against the cap. Pure. Returns (state, detail)."""
    total = int(total_count or 0)
    last = last_reachable_page(per_page)

    if total <= 0:
        return ("no-matches", "no results; the query matches nothing")

    if total > CAP:
        slices = -(-total // CAP)
        return ("capped",
                "total_count is %d and only the first %d are reachable, so %d "
                "match(es) cannot be paged to at any page size. Page %d at "
                "per_page=%d is the last that works; the next one returns 422. "
                "Partition into at least %d narrower queries."
                % (total, CAP, total - CAP, last, per_page, slices))

    if total >= NEAR:
        return ("near-cap",
                "total_count is %d, inside the 1,000-result cap but close to it. "
                "This query starts losing results silently as soon as it grows "
                "past %d; partition it now rather than after."
                % (total, CAP))

    return ("reachable",
            "total_count is %d, all reachable in %d request(s) at per_page=%d."
            % (total, -(-total // min(max(int(per_page), 1), MAX_PER_PAGE)), per_page))


def get(session, path, **params):
    r = session.get(API + path, params=params, timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed or "
                         "revoked")
    if r.status_code == 403:
        raise SystemExit("403 from GitHub. Search has its own small per-minute "
                         "bucket; GET /rate_limit reports resources.search and "
                         "does not itself consume quota")
    if r.status_code == 422:
        raise SystemExit("422 from search: either the query is malformed or it "
                         "already reaches past the 1,000-result cap")
    r.raise_for_status()
    return r.json()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--query", action="append", required=True,
                    help="a search query string. Repeatable, so a partitioned "
                         "query can be checked slice by slice.")
    ap.add_argument("--endpoint", default="issues",
                    choices=["issues", "repositories", "commits", "code", "users",
                             "labels", "topics"],
                    help="which /search/ endpoint to ask")
    ap.add_argument("--per-page", type=int, default=MAX_PER_PAGE,
                    help="the page size your client sends, used for the page "
                         "arithmetic")
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
        "User-Agent": "github-search-cap-audit",
    })

    # Free to ask: /rate_limit is not billed against any bucket, and search is
    # not billed against core, so this is the only cheap way to see the bucket
    # these queries will actually spend.
    quota = get(session, "/rate_limit").get("resources", {}).get("search", {})
    log.info("search bucket: %s of %s remaining, resets at %s",
             quota.get("remaining", "?"), quota.get("limit", "?"),
             quota.get("reset", "?"))

    over = 0
    for q in args.query:
        # per_page=1 is enough: total_count is on every page, and the first item
        # costs less to transfer than a hundred you are not going to read.
        body = get(session, "/search/%s" % args.endpoint, q=q, per_page=1)
        state, detail = reach(body.get("total_count"), args.per_page)
        line = "%-10s %s  %s" % (state, q, detail)
        if state in ("capped", "near-cap"):
            over += 1
            log.warning(line)
            log.warning("  repair: split this query by created: date ranges, by "
                        "repo:, or by label until every slice reports under "
                        "1,000, then union the slices and de-duplicate on id. "
                        "For a full inventory use the matching list endpoint "
                        "instead, which has no such cap.")
        else:
            log.info(line)

    log.info("%d quer(y/ies), %d over or near the %d-result cap",
             len(args.query), over, CAP)
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main())
