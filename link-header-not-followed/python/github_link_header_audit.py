"""Report GitHub list endpoints that advertise pages your client may not read.

Read only. GET requests and nothing else: a token with read access to the
repository is enough, and that is what you should give it. The repair is printed,
never performed.

What this can and cannot see: the API has no idea whether your client follows
rel="next". It can only say whether there is a next page there to be missed, and
how many items are on the far side of it. That is the trap, not the fall.
"""
import argparse
import logging
import os
import re
import sys
from urllib.parse import parse_qs, urlparse

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_link_header_audit")

API = "https://api.github.com"

# Anchored on the angle brackets rather than split on ",". A pagination URL can
# contain a comma of its own -- labels=bug,ci is the everyday case -- and
# splitting the header on commas turns one good link into two broken ones.
LINK = re.compile(r'<([^>]+)>\s*;\s*rel="([^"]+)"')

PROBES = [
    ("pulls", {"state": "open"}),
    ("issues", {"state": "all"}),
    ("branches", {}),
    ("tags", {}),
    ("contributors", {}),
]


def parse_link(header):
    """Parse a Link header into {rel: url}. Pure, so it is tested offline."""
    if not header:
        return {}
    return {rel: url for url, rel in LINK.findall(header)}


def page_number(url):
    """Read the page query parameter out of a pagination URL, or None."""
    if not url:
        return None
    values = parse_qs(urlparse(url).query).get("page") or []
    try:
        return int(values[0])
    except (IndexError, TypeError, ValueError):
        return None


def verdict(links, received, per_page=1):
    """Classify what one list response says about its own completeness.

    Pure, so the rules are visible rather than buried in a request loop.
    Returns (state, detail).

    The states are deliberately three and not two. "more-pages-unsized" is the
    case where rel="next" exists and rel="last" does not: the list is still
    truncated, and a loop that terminates on the missing rel="last" is the same
    bug this note is about.
    """
    if "next" not in links:
        return ("single-page",
                '%d item(s) and no rel="next". One request really is the whole '
                "list here." % received)

    last = page_number(links.get("last"))
    if last is None:
        return ("more-pages-unsized",
                'rel="next" is present and rel="last" is not, so the total is only '
                "knowable by walking it. Terminate on the absence of "
                'rel="next", never on the absence of rel="last".')

    if per_page == 1:
        return ("more-pages",
                "%d item(s) in total. A client that reads the first page and stops "
                "reports %d." % (last, received))

    return ("more-pages",
            "%d page(s) at per_page=%d, so %d to %d item(s) in total. A client "
            "that reads the first page and stops reports %d."
            % (last, per_page, (last - 1) * per_page + 1, last * per_page, received))


def get(session, path, **params):
    r = session.get(API + path, params=params, timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed or "
                         "revoked")
    if r.status_code == 403 and "rate limit" in r.text.lower():
        raise SystemExit("403 rate limited. GET /rate_limit reports the reset time "
                         "and does not itself consume quota")
    if r.status_code == 404:
        raise SystemExit("404 on %s: the repository does not exist, or this token "
                         "cannot see it -- GitHub returns 404 rather than 403 for "
                         "resources you may not know about" % path)
    r.raise_for_status()
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--path", action="append",
                    help="probe this API path instead of the defaults, e.g. "
                         "/repos/o/n/releases. Repeatable.")
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
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": "github-link-header-audit",
    })

    if args.path:
        probes = [(p, {}) for p in args.path]
    else:
        probes = [("/repos/%s/%s" % (args.repo, name), extra)
                  for name, extra in PROBES]

    truncatable = 0
    remaining = "?"
    for path, extra in probes:
        # per_page=1 makes the rel="last" page number the exact item count, for
        # one request and one item of transfer.
        r = get(session, path, per_page=1, **extra)
        remaining = r.headers.get("x-ratelimit-remaining", "?")
        body = r.json()
        received = len(body) if isinstance(body, list) else 0
        state, detail = verdict(parse_link(r.headers.get("Link")), received, 1)

        line = "%-18s %s  %s" % (state, path, detail)
        if state == "single-page":
            log.info(line)
            continue
        truncatable += 1
        log.warning(line)
        log.warning('  repair: follow rel="next" until it is absent -- '
                    "octokit.paginate() in Octokit, the PaginatedList in PyGithub, "
                    "gh api --paginate on the command line. Never build page URLs "
                    "by hand.")

    log.info("%d endpoint(s) probed, %d with pages beyond the first; "
             "x-ratelimit-remaining %s", len(probes), truncatable, remaining)
    return 1 if truncatable else 0


if __name__ == "__main__":
    sys.exit(main())
