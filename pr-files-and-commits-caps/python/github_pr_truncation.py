"""Compare a pull request's own counters against what its lists can return.

Read only. Three GETs per pull request. Nothing is written and the repair is
printed rather than performed.

A pull request object carries changed_files and commits. The two list endpoints
hanging off it do not: .../files stops at 3,000 files and .../commits at 250,
and both answer 200 with a shorter array rather than saying they stopped. The
counters are therefore the only ground truth a client has, and they live at a
different URL from the lists they describe.

What this can and cannot see: the API has no idea how many pages your collector
read. It can state what the pull request declares, how far each list endpoint is
prepared to go, and where those two things cannot be reconciled.

Environment:

    GITHUB_TOKEN    a token with read access to the repository
"""
import argparse
import json
import logging
import math
import os
import re
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_pr_truncation")

API = "https://api.github.com"
UA = "github-pr-truncation/1.0"

MAX_PER_PAGE = 100
# What you get when nobody sets per_page, which is where most of the loss
# happens: long before either ceiling, on any pull request over thirty files.
DEFAULT_PER_PAGE = 30

# The documented ceilings on the two lists hanging off a pull request. Named
# together because the note is about the pair of them and their repairs differ.
CAPS = {"files": 3000, "commits": 250}

# Anchored on the angle brackets rather than split on commas: a pagination URL
# can carry a comma of its own and splitting on it breaks the link in half.
LINK = re.compile(r'<([^>]+)>\s*;\s*rel="([^"]+)"')
PAGE = re.compile(r'[?&]page=(\d+)')


def parse_link(header):
    """Parse a Link header into {rel: url}. Pure."""
    if not header:
        return {}
    return {rel: url for url, rel in LINK.findall(header)}


def page_of(url):
    """The page number inside a pagination URL, or None. Pure."""
    if not url:
        return None
    m = PAGE.search(str(url))
    return int(m.group(1)) if m else None


def cap_for(kind):
    """The documented ceiling on this list, or None if there isn't one. Pure."""
    return CAPS.get(kind)


def pages_needed(total, per_page):
    """Pages required to hold this many items. Pure. None for nonsense input."""
    try:
        n, size = int(total), int(per_page)
    except (TypeError, ValueError):
        return None
    if n < 0 or size < 1:
        return None
    return int(math.ceil(n / float(size)))


def reachable(kind, declared):
    """How many of the declared items the list endpoint can actually hand over."""
    cap = cap_for(kind)
    try:
        n = int(declared)
    except (TypeError, ValueError):
        return None
    if cap is None:
        return n
    return min(n, cap)


def beyond_cap(kind, declared):
    """How many items are unreachable through this endpoint. Pure. 0 when fine."""
    cap = cap_for(kind)
    try:
        n = int(declared)
    except (TypeError, ValueError):
        return 0
    if cap is None:
        return 0
    return max(0, n - cap)


def bounds_from_last(last_page, per_page):
    """The item count a rel=last page number implies, as (low, high). Pure.

    A last page of 3 at per_page=100 means somewhere between 201 and 300 items:
    the final page holds at least one and at most a full page. That band is the
    widest honest statement the header supports, so the counter is only called a
    disagreement when it falls outside it.
    """
    try:
        last, size = int(last_page), int(per_page)
    except (TypeError, ValueError):
        return None
    if last < 1 or size < 1:
        return None
    return ((last - 1) * size + 1, last * size)


def counter_outside_bounds(declared, bounds):
    """Whether the pull request's own count contradicts the page count. Pure."""
    if not bounds:
        return False
    try:
        n = int(declared)
    except (TypeError, ValueError):
        return False
    return n < bounds[0] or n > bounds[1]


def one_page_shortfall(declared, per_page=DEFAULT_PER_PAGE):
    """Items a client reading a single page never sees. Pure."""
    try:
        n, size = int(declared), int(per_page)
    except (TypeError, ValueError):
        return 0
    return max(0, n - max(0, size))


def verdict(kind, declared, last_page=None, per_page=MAX_PER_PAGE):
    """Classify one list against the counter that describes it. Pure.

    Returns (state, detail). The states keep three unreconcilable things apart:
    a count above the endpoint's ceiling, a count the endpoint's own page count
    cannot contain, and a count that is fine but needs more than one page.
    """
    cap = cap_for(kind)
    if cap is None:
        return ("unknown", "%r is not a list this check knows a ceiling for." % kind)
    try:
        n = int(declared)
    except (TypeError, ValueError):
        return ("unknown",
                "the pull request did not report a count for %s, so there is "
                "nothing to reconcile the list against." % kind)
    if n < 0:
        return ("unknown", "a negative count for %s is not a number this check "
                           "can use." % kind)

    over = beyond_cap(kind, n)
    if over:
        return ("beyond-cap",
                "%d %s are declared and the endpoint stops at %d, so %d of them "
                "cannot be read through it at any page size."
                % (n, kind, cap, over))

    bounds = bounds_from_last(last_page, per_page)
    if counter_outside_bounds(n, bounds):
        return ("counter-disagrees",
                "the pull request declares %d %s and the Link header stops at "
                "page %s, which can hold between %d and %d, so the list is "
                "shorter than the counter and something truncated it."
                % (n, kind, last_page, bounds[0], bounds[1]))

    if n > DEFAULT_PER_PAGE:
        return ("multi-page",
                "%d %s across %d page(s) at per_page=%d. A client reading one "
                "page at the default %d sees %d of them and misses %d."
                % (n, kind, pages_needed(n, per_page) or 1, int(per_page),
                   DEFAULT_PER_PAGE, min(n, DEFAULT_PER_PAGE),
                   one_page_shortfall(n)))

    return ("single-page",
            "%d %s fit in one page at any page size, so nothing here is being "
            "truncated today." % (n, kind))


def repair(state, kind):
    """The sentence a reader has to act on. Pure."""
    if state == "beyond-cap" and kind == "files":
        return ("request the pull request with the application/vnd.github.diff "
                "media type and parse the diff. The JSON list will not return "
                "file 3001 however you paginate it.")
    if state == "beyond-cap" and kind == "commits":
        return ("read the branch through GET /repos/{owner}/{repo}/commits with "
                "a sha and a date range, which paginates conventionally and has "
                "no ceiling of its own.")
    if state == "counter-disagrees":
        return ("collect the whole list at per_page=100 and compare the count "
                "you collected against changed_files and commits on the pull "
                "request object, raising rather than logging on a mismatch.")
    if state == "multi-page":
        return ("set per_page=100, follow rel=next to the end, and assert the "
                "collected count against the counter on the pull request "
                "object. The default page size of 30 is where this is lost.")
    if state == "single-page":
        return ("nothing on this pull request. Run the same check against your "
                "largest ones, which are the ones a review bot is trusted on.")
    return "point the check at a pull request this token can read."


def read_cost(prs):
    """Requests this run will spend against the core quota. Pure.

    Three per pull request: the object for its counters, and one page each of
    files and commits for their Link headers.
    """
    return 3 * len(prs or [])


def get(session, path, params=None):
    """One GET. Returns (status, parsed-body-or-None, links)."""
    r = session.get(API + path, params=params or {}, timeout=30)
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
        return r.status_code, r.json(), links
    except ValueError:
        return r.status_code, None, links


def last_page_from(links):
    """The endpoint's own page count, or 1 when it says there is only one. Pure.

    None where there is a next page but no last, because then the page count is
    genuinely unknown and guessing it is how the other notes in this section
    start.
    """
    last = page_of(links.get("last"))
    if last:
        return last
    if "next" in links:
        return None
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--pr", action="append", type=int, required=True,
                    help="pull request number. Repeatable.")
    ap.add_argument("--per-page", type=int, default=MAX_PER_PAGE,
                    help="page size used to probe the lists. 100 is the maximum "
                         "and there is no reason to probe with less.")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    log.info("read cost: %d request(s) against the core hourly quota",
             read_cost(args.pr))

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })

    findings = []
    for number in args.pr:
        base = "/repos/%s/pulls/%d" % (args.repo, number)
        _status, pr, _links = get(session, base)
        if not isinstance(pr, dict):
            continue
        log.info("pull %d declares %s changed file(s) and %s commit(s)",
                 number, pr.get("changed_files"), pr.get("commits"))

        for kind, declared in (("files", pr.get("changed_files")),
                               ("commits", pr.get("commits"))):
            _s, _body, links = get(session, base + "/" + kind,
                                   {"per_page": args.per_page})
            last = last_page_from(links)
            state, detail = verdict(kind, declared, last, args.per_page)
            log.info("%s: %s - %s", kind, state, detail)
            log.info("repair: %s", repair(state, kind))
            findings.append({
                "pull_request": number,
                "list": kind,
                "declared": declared,
                "cap": cap_for(kind),
                "reachable": reachable(kind, declared),
                "unreachable": beyond_cap(kind, declared),
                "endpoint_last_page": last,
                "implied_bounds": bounds_from_last(last, args.per_page),
                "missed_by_one_default_page": one_page_shortfall(declared),
                "state": state,
                "detail": detail,
                "repair": repair(state, kind),
            })

    print(json.dumps({"requests_spent": read_cost(args.pr),
                      "findings": findings}, indent=2, default=str))
    bad = {"beyond-cap", "counter-disagrees", "multi-page"}
    return 1 if any(f["state"] in bad for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
