"""Find GitHub endpoints that ignore the page parameter instead of rejecting it.

Read only. Two GETs per probed path at per_page=1, and nothing is written.

A minority of endpoints do not implement offset pagination. They page with
before/after cursors, or they do not page at all, and they ignore page and
per_page rather than answering 422. So page=2 returns page one, a hand-rolled
loop that stops on a short page never stops, and the same rows are collected
until something kills the job.

Two independent signals are used, because either alone is unsafe. Identical
identifiers across page 1 and page 2 is the symptom, but a feed sorted by
recency can genuinely move between two requests. The parameter names on the
endpoint's own next link do not depend on timing at all: a next URL built from
after= or before= is the endpoint saying which kind of pagination it speaks.

Environment:

    GITHUB_TOKEN    a token with read access to the repository
"""
import argparse
import json
import logging
import os
import re
import sys
from urllib.parse import parse_qs, urlparse

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_page_param_ignored")

API = "https://api.github.com"
UA = "github-page-param-ignored/1.0"

LINK = re.compile(r'<([^>]+)>\s*;\s*rel="([^"]+)"')

# The parameter names that mean each kind of pagination. Cursor names first
# because an endpoint that offers both is a cursor endpoint being polite.
CURSOR_PARAMS = ("after", "before", "cursor")
OFFSET_PARAMS = ("page",)

# Tried in order. Not every list on this API keys its items the same way, and
# comparing whole objects is useless because counters inside an item change
# between two requests while the item stays the same row.
ID_FIELDS = ("id", "node_id", "sha", "url")

PROBES = ["activity", "events"]


def parse_link(header):
    """Parse a Link header into {rel: url}. Pure."""
    if not header:
        return {}
    return {rel: url for url, rel in LINK.findall(header)}


def link_params(links):
    """The query parameter names on the next URL, sorted. Pure."""
    url = (links or {}).get("next")
    if not url:
        return []
    return sorted(parse_qs(urlparse(url).query))


def link_style(links):
    """What the endpoint's own next link is built from. Pure.

    Returns cursor, offset or none. This is the signal that does not depend on
    timing, which is why the definitive verdict requires it.
    """
    names = set(link_params(links))
    if names & set(CURSOR_PARAMS):
        return "cursor"
    if names & set(OFFSET_PARAMS):
        return "offset"
    return "none"


def cursor_hint(links):
    """The cursor parameter this endpoint actually uses, or None. Pure."""
    names = set(link_params(links))
    for name in CURSOR_PARAMS:
        if name in names:
            return name
    return None


def identity(item):
    """A stable identifier for one list item, or None. Pure."""
    if not isinstance(item, dict):
        return None
    for field in ID_FIELDS:
        value = item.get(field)
        if value not in (None, ""):
            return str(value)
    return None


def identities(items):
    """Identifiers for a page, dropping items that have none. Pure."""
    if not isinstance(items, list):
        return []
    return [i for i in (identity(x) for x in items) if i is not None]


def same_rows(first, second):
    """Whether page two is page one, exactly. Pure."""
    return bool(first) and bool(second) and list(first) == list(second)


def overlaps(first, second):
    """Whether the two pages share any row at all. Pure."""
    return bool(set(first or []) & set(second or []))


def verdict(style, first_ids, second_ids):
    """Classify one endpoint from both signals. Pure. Returns (state, detail).

    The two conclusive states require the signals to agree. Where they do not,
    the weaker verdict is returned deliberately: a check that cries wolf on a
    busy feed is a check somebody turns off.
    """
    if not first_ids:
        return ("inconclusive-empty",
                "page 1 returned nothing this check could identify, so there is "
                "no comparison to make. Point it at a path with rows in it.")
    if not second_ids:
        return ("offset-honoured",
                "page 2 came back empty, so the collection ends inside page 1 "
                "and the page parameter is being read.")
    if same_rows(first_ids, second_ids):
        if style in ("cursor", "none"):
            shape = ("built from a cursor" if style == "cursor"
                     else "absent, so there is no next page to follow")
            return ("ignores-page",
                    "page=2 returned the same row(s) as page=1 and the next "
                    "link is %s, so this endpoint does not read page at all. A "
                    "loop that stops on a short page has no terminating "
                    "condition here." % shape)
        return ("suspect-ignores-page",
                "page=2 returned the same row(s) as page=1, but the next link "
                "is still built from page=, so this may be a feed that moved "
                "between the two requests. Re-run it, or add a stable sort, "
                "before treating it as a finding.")
    if overlaps(first_ids, second_ids):
        return ("overlapping-pages",
                "page 1 and page 2 share rows without being identical, which is "
                "an unstable sort rather than an ignored parameter. Paging this "
                "endpoint will double-count and skip.")
    if style == "cursor":
        return ("cursor-pagination",
                "the rows differ and the next link is built from a cursor, so "
                "this endpoint pages correctly and simply not by number. Follow "
                "its next URL rather than incrementing anything.")
    return ("offset-honoured",
            "page 2 returned different rows and the next link is built from "
            "page=, so offset pagination works here.")


def loop_terminates(state):
    """Whether a page-counting loop against this endpoint would ever end. Pure."""
    return state not in ("ignores-page", "suspect-ignores-page")


def repair(state, links=None):
    """The sentence a reader has to act on. Pure."""
    if state == "ignores-page":
        cursor = cursor_hint(links)
        if cursor:
            return ("follow the next URL from the Link header verbatim, using "
                    "%s=. Do not construct it, and do not send page: the value "
                    "is opaque and incrementing anything here is meaningless."
                    % cursor)
        return ("stop paging this endpoint by number. It advertises no next "
                "page, so one request is what it offers, and the GraphQL "
                "equivalent with after: $cursor is the way to walk more.")
    if state == "suspect-ignores-page":
        return ("re-run the check, or add a deterministic sort, before changing "
                "any code. Identical rows on a recency-ordered feed can be two "
                "requests a second apart rather than an ignored parameter.")
    if state == "overlapping-pages":
        return ("sort deterministically before paging, or switch to the cursor "
                "form. Offset paging over a feed that reorders will double-count "
                "some rows and miss others whatever the page size.")
    if state == "cursor-pagination":
        return ("follow the next URL from the Link header verbatim. It already "
                "carries the cursor, and building it yourself is the only way "
                "to get this wrong.")
    if state == "inconclusive-empty":
        return "point the check at a path that has rows in it."
    return "nothing."


def read_cost(paths):
    """Requests this run will spend against the core quota. Pure."""
    return 2 * len(paths or [])


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
                    help="probe this API path instead of the defaults. Repeatable.")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    paths = args.path or ["/repos/%s/%s" % (args.repo, name) for name in PROBES]
    log.info("read cost: %d request(s) against the core hourly quota",
             read_cost(paths))

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
        status1, first, links = get(session, path, {"per_page": 1, "page": 1})
        if first is None:
            log.info("%s returned %d; skipping it", path, status1)
            continue
        status2, second, _links2 = get(session, path, {"per_page": 1, "page": 2})
        first_ids, second_ids = identities(first), identities(second)
        style = link_style(links)
        state, detail = verdict(style, first_ids, second_ids)

        if same_rows(first_ids, second_ids):
            log.info("%s: page=1 and page=2 returned the same id(s)", path)
        log.info("%s: %s", state, detail)
        log.info("a page-counting loop here %s",
                 "terminates" if loop_terminates(state) else "never terminates")
        log.info("repair: %s", repair(state, links))

        findings.append({
            "path": path,
            "status": [status1, status2],
            "next_link_params": link_params(links),
            "link_style": style,
            "cursor_parameter": cursor_hint(links),
            "page_1_ids": first_ids,
            "page_2_ids": second_ids,
            "identical": same_rows(first_ids, second_ids),
            "loop_terminates": loop_terminates(state),
            "state": state,
            "detail": detail,
        })

    print(json.dumps({"requests_spent": read_cost(paths),
                      "findings": findings}, indent=2, default=str))
    bad = {"ignores-page", "suspect-ignores-page", "overlapping-pages"}
    return 1 if any(f["state"] in bad for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
