"""Report how many requests an unset per_page is costing on each list endpoint.

Read only. GET requests and nothing else: a token with read access is enough.
The repair is printed, never performed.

This is a cost check, not a correctness one. Raising per_page does not make a
client that ignores the Link header correct; it makes it wrong by 100 instead
of by 30.
"""
import argparse
import logging
import os
import re
import sys
from urllib.parse import parse_qs, urlparse

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_per_page_audit")

API = "https://api.github.com"
LINK = re.compile(r'<([^>]+)>\s*;\s*rel="([^"]+)"')

MAX_PER_PAGE = 100
DEFAULT_PER_PAGE = 30

PROBES = [
    ("issues", {"state": "all"}),
    ("pulls", {"state": "all"}),
    ("commits", {}),
    ("branches", {}),
    ("tags", {}),
]


def pages_for(items, per_page):
    """Requests needed to read `items` at `per_page`. Pure.

    Clamps to 100 the way the API does rather than the way the caller hoped:
    per_page above the maximum is silently reduced, not rejected, so pretending
    500 works here would hide exactly the mistake this script exists to find.
    """
    size = min(max(int(per_page or DEFAULT_PER_PAGE), 1), MAX_PER_PAGE)
    items = int(items or 0)
    if items <= 0:
        return 0
    return -(-items // size)


def verdict(items, per_page=DEFAULT_PER_PAGE):
    """Classify one endpoint's page-size arithmetic. Pure. Returns (state, detail)."""
    items = int(items or 0)
    if items <= 0:
        return ("empty", "no items; nothing to page and nothing to save")

    now = pages_for(items, per_page)
    best = pages_for(items, MAX_PER_PAGE)

    if now == best:
        if int(per_page or DEFAULT_PER_PAGE) > MAX_PER_PAGE:
            return ("at-maximum",
                    "%d item(s) in %d request(s). per_page=%s is above the maximum "
                    "and was clamped to 100, which costs nothing here but will "
                    "mislead any loop that trusts the number it asked for."
                    % (items, now, per_page))
        return ("at-maximum" if now > 1 else "single-page",
                "%d item(s) in %d request(s); per_page=100 would not improve on it."
                % (items, now))

    saved = now - best
    return ("wasteful",
            "%d item(s): %d request(s) at per_page=%d, %d at per_page=100. "
            "%d request(s) of quota and %d round trip(s) wasted on every full "
            "pass (%.0f%%)."
            % (items, now, int(per_page), best, saved, saved, 100.0 * saved / now))


def parse_link(header):
    if not header:
        return {}
    return {rel: url for url, rel in LINK.findall(header)}


def page_number(url):
    if not url:
        return None
    values = parse_qs(urlparse(url).query).get("page") or []
    try:
        return int(values[0])
    except (IndexError, TypeError, ValueError):
        return None


def get(session, path, **params):
    r = session.get(API + path, params=params, timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed or "
                         "revoked")
    if r.status_code == 403 and "rate limit" in r.text.lower():
        raise SystemExit("403 rate limited. GET /rate_limit reports the reset time "
                         "and does not itself consume quota")
    r.raise_for_status()
    return r


def count_items(session, path, extra):
    """Exact item count in at most two requests.

    Page one at the maximum page size gives rel="last"; reading that last page
    gives the remainder. (last - 1) * 100 + len(last page) is the total, with no
    estimation anywhere in it.
    """
    first = get(session, path, per_page=MAX_PER_PAGE, **extra)
    body = first.json()
    if not isinstance(body, list):
        return None
    last = page_number(parse_link(first.headers.get("Link")).get("last"))
    if last is None or last <= 1:
        return len(body)
    tail = get(session, path, per_page=MAX_PER_PAGE, page=last, **extra).json()
    return (last - 1) * MAX_PER_PAGE + len(tail)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--per-page", type=int, default=DEFAULT_PER_PAGE,
                    help="the page size your client currently sends "
                         "(default 30, which is what an unset per_page means)")
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
        "User-Agent": "github-per-page-audit",
    })

    wasteful = 0
    recoverable = 0
    for name, extra in PROBES:
        path = "/repos/%s/%s" % (args.repo, name)
        items = count_items(session, path, extra)
        if items is None:
            log.info("%-12s %s  not a list endpoint, skipped", "skipped", path)
            continue
        state, detail = verdict(items, args.per_page)
        line = "%-12s %s  %s" % (state, path, detail)
        if state == "wasteful":
            wasteful += 1
            recoverable += pages_for(items, args.per_page) - pages_for(items, MAX_PER_PAGE)
            log.warning(line)
            log.warning("  repair: add per_page=100 to this request. It returns the "
                        "same data for the same one request per page.")
        else:
            log.info(line)

    log.info("%d endpoint(s), %d wasteful, %d request(s) per pass recoverable",
             len(PROBES), wasteful, recoverable)
    return 1 if wasteful else 0


if __name__ == "__main__":
    sys.exit(main())
