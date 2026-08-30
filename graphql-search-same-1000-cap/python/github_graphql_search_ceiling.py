"""Show that GraphQL search stops at the same 1,000 results REST does.

Read only, and queries only. GitHub's GraphQL endpoint takes its document in
the request body, so a read travels by POST there exactly as a write would;
that is a transport detail, not a licence to write. This script parses the
document it is about to send and refuses to open a socket if it contains a
mutation or a subscription.

GraphQL's search connection is served by the same index as GET /search/*, and
inherits the same ceiling of roughly 1,000 retrievable results per query. The
difference is how it says so: REST returns 422 Validation Failed on page 11,
GraphQL just sets pageInfo.hasNextPage to false and lets the walk finish
normally with issueCount still reporting the full match count beside it.

What this can and cannot see: the API cannot tell you whether your client
compares the two numbers. What it can do is walk the connection until it stops,
report where it stopped against what the index matched, and say plainly that no
error was raised.

Environment:

    GITHUB_TOKEN    a token with read access to the GraphQL API
"""
import argparse
import json
import logging
import math
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_graphql_search_ceiling")

API = "https://api.github.com"
UA = "github-graphql-search-ceiling/1.0"

# The retrievable-result ceiling on the search index. Identical on both APIs
# because it is a property of the index rather than of the protocol.
SEARCH_RESULT_CEILING = 1000

# The largest page any GraphQL connection will serve.
MAX_PAGE_SIZE = 100

# One search connection at first: 100 costs one point.
POINTS_PER_PAGE = 1

SEARCH_QUERY = (
    "query($q: String!, $type: SearchType!, $after: String) {"
    " search(query: $q, type: $type, first: 100, after: $after) {"
    " issueCount repositoryCount userCount"
    " pageInfo { hasNextPage endCursor }"
    " nodes { __typename } } }"
)

# The connections that answer an inventory question without going through the
# index at all, and therefore without a ceiling.
TYPED_CONNECTIONS = {
    "ISSUE": "repository.issues or repository.pullRequests",
    "REPOSITORY": "organization.repositories or user.repositories",
    "USER": "organization.membersWithRole",
    "DISCUSSION": "repository.discussions",
}


def strip_noise(document):
    """Remove GraphQL comments and string literals from a document. Pure."""
    src = str(document or "")
    out = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch == "#":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if src.startswith('"""', i):
            j = src.find('"""', i + 3)
            i = n if j < 0 else j + 3
            out.append(" ")
            continue
        if ch == '"':
            i += 1
            while i < n and src[i] != '"':
                i += 2 if src[i] == "\\" else 1
            i += 1
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def operations(document):
    """The top-level operations in a document, in order. Pure."""
    src = strip_noise(document)
    ops, depth, word, declared = [], 0, "", None
    for ch in src + " ":
        if ch.isalnum() or ch == "_":
            word += ch
            continue
        if word:
            if depth == 0 and word in ("query", "mutation", "subscription", "fragment"):
                declared = word
            word = ""
        if ch == "{":
            if depth == 0:
                ops.append(declared or "query")
                declared = None
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
    return ops


def refusal(document):
    """Why this document will not be sent, or None if it is a read. Pure."""
    ops = operations(document)
    if not ops:
        return "the document contains no operation to send."
    for kind in ("mutation", "subscription"):
        if kind in ops:
            return ("the document contains a %s. This script sends queries only: "
                    "a query is a read, and the section it belongs to promises "
                    "its scripts never write." % kind)
    return None


def reachable(total):
    """How many of the matches can actually be paged to. Pure."""
    try:
        total = max(0, int(total))
    except (TypeError, ValueError):
        return 0
    return min(total, SEARCH_RESULT_CEILING)


def unreachable(total):
    """How many matches exist that no cursor will ever reach. Pure."""
    try:
        total = max(0, int(total))
    except (TypeError, ValueError):
        return 0
    return max(0, total - SEARCH_RESULT_CEILING)


def pages_to_ceiling(page_size=MAX_PAGE_SIZE):
    """How many pages of this size fit under the ceiling. Pure."""
    size = min(max(1, int(page_size or 1)), MAX_PAGE_SIZE)
    return math.ceil(SEARCH_RESULT_CEILING / size)


def slices_needed(total):
    """How many under-the-ceiling slices a partition needs. Pure."""
    try:
        total = max(0, int(total))
    except (TypeError, ValueError):
        return 0
    return math.ceil(total / SEARCH_RESULT_CEILING) if total else 0


def typed_connection_for(search_type):
    """The ceiling-free connection that answers the same question. Pure."""
    return TYPED_CONNECTIONS.get(str(search_type or "").upper(),
                                 "the typed connection for this object type")


def truncation_signal(protocol):
    """How each API announces the same ceiling. Pure.

    Kept as data because the whole note is the difference between these two
    sentences, and a reader who has only ever seen the first one does not
    believe the second until it is written down next to it.
    """
    if str(protocol).lower() == "rest":
        return ("422 Validation Failed on page 11, with the message "
                "\"Only the first 1000 search results are available\".")
    return ("no error at all. pageInfo.hasNextPage turns false and the walk "
            "terminates the way a complete walk terminates.")


def classify_walk(total, collected, has_next_page, pages_walked, max_pages):
    """How this walk ended, and whether that ending was honest. Pure.

    Returns (state, detail). The two states that both end with hasNextPage
    false are the point: one of them is a complete answer and one of them is
    six per cent of an answer, and they are the same shape of response.
    """
    total = reachable(total) + unreachable(total)
    collected = max(0, int(collected or 0))
    if has_next_page and pages_walked >= max_pages:
        return ("stopped-early-by-request",
                "the walk stopped at the --max-pages limit with more pages "
                "available, so nothing about the ceiling is proved yet. "
                "%d of %d node(s) collected." % (collected, total))
    if has_next_page:
        return ("still-paging",
                "the connection still reports another page. Keep walking or "
                "raise --max-pages.")
    if collected >= SEARCH_RESULT_CEILING and total > collected:
        return ("ceiling-hit-silently",
                "pagination stopped after %d node(s) with the index reporting "
                "%d match(es). No error was raised and hasNextPage simply "
                "turned false." % (collected, total))
    if total > collected:
        return ("truncated-early",
                "the walk ended with %d of %d match(es) and below the ceiling, "
                "which is not this note: check for a timed-out search or a "
                "filter applied after the count." % (collected, total))
    return ("complete",
            "%d node(s) collected against %d match(es). This query is under "
            "the ceiling and the answer is whole." % (collected, total))


def repair(state, total, search_type):
    """The sentence a reader has to act on. Pure."""
    if state == "ceiling-hit-silently":
        return ("for an inventory use the typed connection %s, which has no "
                "ceiling. For a genuine search, partition into at least %d "
                "slice(s) by created: date range and union them."
                % (typed_connection_for(search_type), slices_needed(total)))
    if state == "truncated-early":
        return ("see /github/search-incomplete-results/ -- a search that ends "
                "below the ceiling ended for a different reason, and that one "
                "is not deterministic.")
    if state == "still-paging":
        return ("nothing yet. Walk to the end, or read issueCount on page one "
                "and compare it against %d." % SEARCH_RESULT_CEILING)
    if state == "stopped-early-by-request":
        return ("raise --max-pages to at least %d to reach the ceiling, or "
                "trust issueCount, which already tells you."
                % pages_to_ceiling())
    return ("request issueCount alongside nodes and refuse to publish a result "
            "set shorter than it without labelling it truncated.")


def point_cost(max_pages):
    """The most this run can spend against the hourly budget. Pure."""
    return max(0, int(max_pages or 0)) * POINTS_PER_PAGE


def run_query(session, document, variables):
    """Send one search page. Returns (status, body-or-None).

    A GraphQL query is a read; POST is only how the document reaches the
    endpoint, which is why the verb is written here beside the URL rather than
    hidden in a constant where it could be mistaken for a write path.
    """
    r = session.post(API + "/graphql",
                     json={"query": document, "variables": variables or {}},
                     timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed "
                         "or revoked")
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


def match_count(search, search_type):
    """The index's own count for this search type. Pure."""
    if not isinstance(search, dict):
        return 0
    key = {"ISSUE": "issueCount", "REPOSITORY": "repositoryCount",
           "USER": "userCount"}.get(str(search_type or "").upper(), "issueCount")
    return int(search.get(key) or 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--query", required=True,
                    help="the search string, e.g. 'org:acme is:issue is:open'")
    ap.add_argument("--type", default="ISSUE",
                    choices=["ISSUE", "REPOSITORY", "USER", "DISCUSSION"],
                    help="the GraphQL SearchType to use")
    ap.add_argument("--max-pages", type=int, default=11,
                    help="how many pages of 100 to walk. Ten reaches the "
                         "ceiling; eleven proves the walk stops there.")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    why_not = refusal(SEARCH_QUERY)
    if why_not:
        log.error("refusing to send: %s", why_not)
        return 2

    log.info("point cost: up to %d point(s) against the 5,000/hour GraphQL budget",
             point_cost(args.max_pages))

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })

    cursor, collected, total, pages = None, 0, 0, 0
    has_next = False
    while pages < args.max_pages:
        status, body = run_query(session, SEARCH_QUERY,
                                 {"q": args.query, "type": args.type,
                                  "after": cursor})
        if not isinstance(body, dict) or body.get("errors"):
            log.error("the search itself failed: HTTP %s %s", status,
                      json.dumps((body or {}).get("errors", []))[:300])
            return 2
        search = (body.get("data") or {}).get("search") or {}
        nodes = search.get("nodes") or []
        info = search.get("pageInfo") or {}
        pages += 1
        collected += len(nodes)
        total = match_count(search, args.type)
        has_next = bool(info.get("hasNextPage"))
        cursor = info.get("endCursor")
        log.info("page %d: %d node(s), collected=%d, matches=%d, hasNextPage=%s",
                 pages, len(nodes), collected, total, "yes" if has_next else "no")
        if not has_next:
            break

    state, detail = classify_walk(total, collected, has_next, pages, args.max_pages)
    log.info("%s: %s", state, detail)
    log.info("reachable: %d    unreachable: %d", reachable(total), unreachable(total))
    log.info("the REST twin of this stop is %s", truncation_signal("rest"))
    log.info("here it is %s", truncation_signal("graphql"))
    log.info("repair: %s", repair(state, total, args.type))

    print(json.dumps({
        "points_spent": pages * POINTS_PER_PAGE,
        "search": args.query,
        "type": args.type,
        "matches": total,
        "collected": collected,
        "pages_walked": pages,
        "has_next_page": has_next,
        "reachable": reachable(total),
        "unreachable": unreachable(total),
        "slices_needed": slices_needed(total),
        "typed_connection": typed_connection_for(args.type),
        "state": state,
        "detail": detail,
        "repair": repair(state, total, args.type),
    }, indent=2, default=str))
    return 1 if state == "ceiling-hit-silently" else 0


if __name__ == "__main__":
    sys.exit(main())
