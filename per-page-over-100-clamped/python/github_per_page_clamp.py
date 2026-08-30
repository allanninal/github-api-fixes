"""Show that per_page above the maximum is reduced rather than refused.

Read only. One GET per probed path, plus one more per path with --confirm.
Nothing is written and the repair is printed rather than performed.

GitHub caps per_page at 100 and enforces the cap by silently lowering the value:
a request for 500 is served as a request for 100 with a success status and no
warning anywhere in the response. That is harmless on its own. It becomes data
loss in a client that decides it has reached the last page by noticing it got
fewer items than it asked for, because 100 is fewer than 500 and the loop stops
with four fifths of the collection unread.

What this can and cannot see: the API has no idea which predicate your client
terminates on. It can show that the clamp happened here and that there is more
data behind the shortened page. That is the trap, not the fall.

Environment:

    GITHUB_TOKEN    a token with read access to the repository
"""
import argparse
import json
import logging
import os
import re
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_per_page_clamp")

API = "https://api.github.com"
UA = "github-per-page-clamp/1.0"

# The documented ceiling on a page. Named rather than inlined because it turns up
# in the output and a reader comparing this against the documentation should find
# it in one place.
MAX_PER_PAGE = 100

# Anchored on the angle brackets rather than split on commas. A pagination URL
# can contain a comma of its own, labels=bug,ci being the everyday case, and
# splitting the header on commas turns one good link into two broken ones.
LINK = re.compile(r'<([^>]+)>\s*;\s*rel="([^"]+)"')

PROBES = ["issues", "pulls", "branches"]


def parse_link(header):
    """Parse a Link header into {rel: url}. Pure."""
    if not header:
        return {}
    return {rel: url for url, rel in LINK.findall(header)}


def clamped_to(requested):
    """The page size GitHub will actually use for this request. Pure.

    None for anything that is not a usable page size, so a bad argument is
    reported rather than silently treated as a finding.
    """
    try:
        n = int(requested)
    except (TypeError, ValueError):
        return None
    if n < 1:
        return None
    return min(n, MAX_PER_PAGE)


def is_over_maximum(requested):
    """Whether this value will be lowered before it is served. Pure."""
    try:
        return int(requested) > MAX_PER_PAGE
    except (TypeError, ValueError):
        return False


def stops_on_short_page(requested, received):
    """The buggy predicate: fewer items than asked for, so that was the end.

    Pure, and written out under its own name on purpose. It is the whole bug,
    and it is much easier to argue about when it is a function with a name than
    when it is an inequality inside somebody's while loop.
    """
    size = clamped_to(requested)
    try:
        got = int(received)
    except (TypeError, ValueError):
        return False
    if size is None:
        return False
    try:
        return got < int(requested)
    except (TypeError, ValueError):
        return False


def stops_on_missing_next(links):
    """The correct predicate: the header no longer advertises a next page. Pure."""
    return "next" not in (links or {})


def predicates_disagree(requested, received, links):
    """Whether the short-page check would stop while the header says otherwise."""
    return stops_on_short_page(requested, received) and not stops_on_missing_next(links)


def verdict(requested, received, links):
    """Classify one response. Pure. Returns (state, detail).

    The states keep two kinds of short page apart. One is losing data right now.
    The other is a collection that happens to end on the boundary, which is the
    same trap with the spring not yet released.
    """
    size = clamped_to(requested)
    if size is None or received is None:
        return ("unknown",
                "the request was not answered in a form this check can read.")
    try:
        got = int(received)
    except (TypeError, ValueError):
        return ("unknown",
                "the request was not answered in a form this check can read.")
    more = not stops_on_missing_next(links)
    over = is_over_maximum(requested)

    if predicates_disagree(requested, received, links):
        if over and got == MAX_PER_PAGE:
            return ("clamped-and-truncated",
                    'per_page=%s was reduced to %d and rel="next" is present, so '
                    "a client that stops on a short page stops here with more to "
                    "read." % (requested, MAX_PER_PAGE))
        return ("smaller-maximum",
                "per_page=%s was asked for and %d item(s) came back with "
                'rel="next" still present, so this endpoint serves a smaller '
                "page than you requested and a short-page check stops here too."
                % (requested, got))
    if over and got == MAX_PER_PAGE:
        return ("clamped-at-boundary",
                "per_page=%s was reduced to %d and there is no next page, so "
                "this collection happens to end exactly on the boundary. The "
                "clamp is real and the truncation starts on item %d."
                % (requested, MAX_PER_PAGE, MAX_PER_PAGE + 1))
    if over:
        return ("clamped-untested",
                "per_page=%s was reduced to %d, but only %d item(s) exist here, "
                "so the truncation cannot be shown on this path. The clamp still "
                "applies to every path that grows past %d."
                % (requested, MAX_PER_PAGE, got, MAX_PER_PAGE))
    if more:
        return ("within-cap-more-pages",
                'per_page=%s was served in full and rel="next" is present. The '
                "short-page check agrees with the header here, which is luck "
                "rather than correctness." % requested)
    return ("within-cap-complete",
            "per_page=%s was served in full and there is no next page. One "
            "request really is the whole list here." % requested)


def repair(state):
    """The sentence a reader has to act on. Pure."""
    if state in ("clamped-and-truncated", "clamped-at-boundary", "clamped-untested"):
        return ("send per_page=100 and terminate on the absence of rel=\"next\" "
                "in the Link header. Asking for more than 100 buys nothing: not "
                "a bigger page, not fewer requests, not an error telling you so.")
    if state == "smaller-maximum":
        return ("this endpoint serves a smaller page than 100, so hard-coding "
                "any page size as your terminating condition is unsafe here. "
                "Follow rel=\"next\" until it is absent.")
    if state == "within-cap-more-pages":
        return ("nothing on the page size. Check that the loop terminates on "
                "the missing rel=\"next\" rather than on the page length: the "
                "two agree on this response and will part company on a clamp.")
    if state == "within-cap-complete":
        return "nothing."
    return "point the check at a path this token can list."


def read_cost(paths, confirm=False):
    """Requests this run will spend against the core quota. Pure."""
    n = len(paths or [])
    return n * (2 if confirm else 1)


def get(session, path, params):
    """One GET. Returns (status, items-or-None, links)."""
    r = session.get(API + path, params=params, timeout=30)
    links = parse_link(r.headers.get("Link"))
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed "
                         "or revoked")
    if r.status_code == 403 and "rate limit" in r.text.lower():
        raise SystemExit("403 rate limited. GET /rate_limit reports the reset "
                         "time and does not itself consume quota")
    if r.status_code != 200:
        log.info("%s returned %d; skipping it", path, r.status_code)
        return r.status_code, None, links
    try:
        body = r.json()
    except ValueError:
        return r.status_code, None, links
    return r.status_code, body if isinstance(body, list) else None, links


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--path", action="append",
                    help="probe this API path instead of the defaults, e.g. "
                         "/repos/o/n/releases. Repeatable.")
    ap.add_argument("--per-page", type=int, default=500,
                    help="the page size to ask for. The default is deliberately "
                         "above the maximum, which is the whole point.")
    ap.add_argument("--confirm", action="store_true",
                    help="spend a second request per path at per_page=100 to "
                         "show the honest page size beside the clamped one")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    paths = args.path or ["/repos/%s/%s" % (args.repo, name) for name in PROBES]
    log.info("read cost: %d request(s) against the core hourly quota",
             read_cost(paths, args.confirm))

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })

    findings = []
    for path in paths:
        status, items, links = get(session, path, {"per_page": args.per_page})
        if items is None:
            continue
        state, detail = verdict(args.per_page, len(items), links)
        log.info("%s: asked for %s, received %d", path, args.per_page, len(items))
        log.info("%s: %s", state, detail)
        log.info("repair: %s", repair(state))

        honest = None
        if args.confirm:
            _s, confirmed, _l = get(session, path, {"per_page": MAX_PER_PAGE})
            honest = len(confirmed) if confirmed is not None else None
            if honest is not None:
                log.info("%s: at per_page=%d the same call returns %d item(s)",
                         path, MAX_PER_PAGE, honest)

        findings.append({
            "path": path,
            "status": status,
            "requested": args.per_page,
            "effective_page_size": clamped_to(args.per_page),
            "received": len(items),
            "rels": sorted(links),
            "short_page_check_stops": stops_on_short_page(args.per_page, len(items)),
            "header_check_stops": stops_on_missing_next(links),
            "predicates_disagree": predicates_disagree(args.per_page, len(items), links),
            "at_per_page_100": honest,
            "state": state,
            "detail": detail,
        })

    print(json.dumps({"requests_spent": read_cost(paths, args.confirm),
                      "findings": findings}, indent=2, default=str))
    bad = {"clamped-and-truncated", "smaller-maximum", "clamped-at-boundary"}
    return 1 if any(f["state"] in bad for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
