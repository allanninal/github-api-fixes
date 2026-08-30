"""Sort GitHub list endpoints into the ones you can index and the ones you can only walk.

Read only. One GET per probed path at per_page=1, which is the cheapest request
that still produces a Link header. No items are read and nothing is written.

GitHub includes rel="last" only when it can calculate a final page. On some
endpoints it cannot, so the header carries rel="next" and no way to know how far
the list goes. A pager that walks the list is unaffected. A pager that indexes it
-- a progress bar, a fan-out over a page range, a jump to the last page -- either
raises on the missing key or, far more often, defaults the missing count to 1 and
reports a single page as the whole collection.

What this can and cannot see: the API cannot tell whether your pager needs a page
count. It can say which of your endpoints will give you one. That is why the
output is a capability list rather than an accusation.

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
log = logging.getLogger("github_rel_last_absent")

API = "https://api.github.com"
UA = "github-rel-last-absent/1.0"

# Anchored on the angle brackets rather than split on commas, because a
# pagination URL can carry a comma of its own inside a query parameter.
LINK = re.compile(r'<([^>]+)>\s*;\s*rel="([^"]+)"')

PROBES = ["issues", "pulls", "branches", "events", "commits"]

# What each shape of header actually supports. Written as data rather than as a
# chain of ifs because the point of the script is the table, not the verdict.
CAPABILITIES = {
    "indexable": {"walk": True, "page_count": True, "progress_bar": True,
                  "parallel_fanout": True, "jump_to_last": True},
    "walk-only": {"walk": True, "page_count": False, "progress_bar": False,
                  "parallel_fanout": False, "jump_to_last": False},
    "single-page": {"walk": True, "page_count": True, "progress_bar": True,
                    "parallel_fanout": False, "jump_to_last": False},
}

PATTERN_NAMES = {
    "page_count": "page count",
    "progress_bar": "progress bar",
    "parallel_fanout": "parallel fan-out",
    "jump_to_last": "jump to last",
}


def parse_link(header):
    """Parse a Link header into {rel: url}. Pure."""
    if not header:
        return {}
    return {rel: url for url, rel in LINK.findall(header)}


def rels(links):
    """The rel names present, sorted. Pure, and the whole evidence base."""
    return sorted(links or {})


def page_param(url):
    """The page query parameter on a pagination URL, or None. Pure."""
    if not url:
        return None
    values = parse_qs(urlparse(url).query).get("page") or []
    try:
        return int(values[0])
    except (IndexError, TypeError, ValueError):
        return None


def pagination_style(links):
    """One of indexable, walk-only, single-page. Pure.

    Three states rather than two on purpose. A boolean check collapses walk-only
    into single-page, which is exactly the mistake this note is about.
    """
    links = links or {}
    if "last" in links:
        return "indexable"
    if "next" in links:
        return "walk-only"
    return "single-page"


def page_count(links):
    """The number of pages, or None where it cannot be known. Pure.

    None is the honest answer on a walk-only endpoint, and callers are expected
    to handle it rather than coerce it.
    """
    if pagination_style(links) == "single-page":
        return 1
    return page_param((links or {}).get("last"))


def naive_page_count(links):
    """The page count the careless way: a missing value becomes 1. Pure.

    This is not a helper. It is the bug, kept under its own name so the script
    can print it beside the careful answer and let the difference do the work.
    """
    return page_count(links) or 1


def item_count(links, per_page):
    """Total items, but only where the endpoint can be indexed. Pure."""
    pages = page_count(links)
    try:
        size = int(per_page)
    except (TypeError, ValueError):
        return None
    if pages is None or size != 1:
        return None
    return pages


def capabilities(style):
    """What a pager may rely on against an endpoint of this shape. Pure."""
    return dict(CAPABILITIES.get(style, CAPABILITIES["walk-only"]))


def unavailable(style):
    """The named patterns that do not work here, in a fixed order. Pure."""
    caps = capabilities(style)
    return [PATTERN_NAMES[k] for k in ("page_count", "progress_bar",
                                       "parallel_fanout", "jump_to_last")
            if not caps[k]]


def verdict(links, per_page=1):
    """Classify one endpoint's header. Pure. Returns (state, detail)."""
    style = pagination_style(links)
    if style == "walk-only":
        return (style,
                'rel="next" is present and rel="last" is not, so the size of '
                "this list is only knowable by walking it. A careful page count "
                "says unknown here; code that defaults a missing count to 1 "
                "reports %d page." % naive_page_count(links))
    if style == "indexable":
        total = item_count(links, per_page)
        return (style,
                'rel="last" is present, so this endpoint can be indexed: %s '
                "page(s) at per_page=%s%s. That number is computed per request "
                "and moves between calls, so it is a display value rather than "
                "a bound." % (page_count(links), per_page,
                              ", which is %d item(s)" % total if total else ""))
    return (style,
            'neither rel="next" nor rel="last" is present. One request is the '
            "whole list here, and nothing about paging applies.")


def repair(state):
    """The sentence a reader has to act on. Pure."""
    if state == "walk-only":
        return ('terminate on the absence of rel="next" and never require '
                'rel="last". Drop the progress bar or make it indeterminate, '
                "and replace any fan-out over a page range with a sequential "
                "walk that follows the next URL exactly as given.")
    if state == "indexable":
        return ('nothing, provided rel="last" is treated as a snapshot. Do not '
                "cache it as the size of the job, and do not let its absence on "
                "some other endpoint default to 1.")
    return "nothing."


def read_cost(paths):
    """Requests this run will spend against the core quota. Pure."""
    return len(paths or [])


def get(session, path, per_page):
    """One GET. Returns (status, links)."""
    r = session.get(API + path, params={"per_page": per_page}, timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed "
                         "or revoked")
    if r.status_code == 403 and "rate limit" in r.text.lower():
        raise SystemExit("403 rate limited. GET /rate_limit reports the reset "
                         "time and does not itself consume quota")
    return r.status_code, parse_link(r.headers.get("Link"))


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
        status, links = get(session, path, 1)
        if status != 200:
            log.info("%s returned %d; skipping it", path, status)
            continue
        state, detail = verdict(links, 1)
        log.info("%s: rels %s -> %s", path, ", ".join(rels(links)) or "none", state)
        log.info("%s: %s", state, detail)
        missing = unavailable(state)
        if missing:
            log.info("unavailable here: %s", ", ".join(missing))
        log.info("repair: %s", repair(state))
        findings.append({
            "path": path,
            "rels": rels(links),
            "style": state,
            "pages": page_count(links),
            "pages_if_missing_defaults_to_one": naive_page_count(links),
            "items": item_count(links, 1),
            "capabilities": capabilities(state),
            "unavailable": missing,
            "detail": detail,
        })

    print(json.dumps({"requests_spent": read_cost(paths),
                      "findings": findings}, indent=2, default=str))
    return 1 if any(f["style"] == "walk-only" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
