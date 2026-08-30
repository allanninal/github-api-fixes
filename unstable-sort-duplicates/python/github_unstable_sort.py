"""Show that a paginated walk is being reordered underneath itself.

Read only. One GET per page per walk, two walks by default. Nothing is written
and the repair is printed rather than performed.

Offset pagination is only stable if the ordering is. page=2 means "items 101 to
200 in the current sort", evaluated when page two is asked for, so an item that
moves between two requests shifts everything behind it and the record on the
boundary is never returned at all.

What this can and cannot see: the exposure is readable from your own request,
because the sort key is in it. The damage is intermittent by nature, so an empty
diff means a quiet window rather than a safe walk, and the script says which of
the two it is looking at.

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
log = logging.getLogger("github_unstable_sort")

API = "https://api.github.com"
UA = "github-unstable-sort/1.0"

MAX_PER_PAGE = 100

# Keys whose value changes while you are reading the collection. A row sorted on
# one of these can move in either direction at any moment.
MUTABLE_SORTS = {"updated", "pushed", "comments", "popularity", "long-running",
                 "reactions", "interactions", "best-match", "relevance", "stars",
                 "forks", "help-wanted-issues"}

# Keys that are set once and never move afterwards.
IMMUTABLE_SORTS = {"created", "full_name", "id"}

# What GitHub applies when a request names a sort but no direction. Descending
# is the common default and it is the one that shifts a window.
DEFAULT_DIRECTION = "desc"

LINK = re.compile(r'<([^>]+)>\s*;\s*rel="([^"]+)"')


def parse_link(header):
    """Parse a Link header into {rel: url}. Pure."""
    if not header:
        return {}
    return {rel: url for url, rel in LINK.findall(header)}


def normalize(ids):
    """Ids as strings, so two walks can be compared and sorted the same way."""
    return [str(i) for i in (ids or [])]


def sort_kind(sort):
    """Whether this sort key moves while you read. Pure."""
    key = str(sort or "").strip().lower()
    if key in MUTABLE_SORTS:
        return "mutable"
    if key in IMMUTABLE_SORTS:
        return "immutable"
    return "unknown"


def walk_risk(sort, direction=None):
    """What a walk over this ordering can lose. Pure. Returns (risk, detail).

    Three outcomes, not two, and the middle one is the reason this function
    exists. An immutable key descending shifts your window when a row is
    inserted at the head, which repeats records and never hides one. A mutable
    key lets a row move past your read position, which hides it for good.
    """
    kind = sort_kind(sort)
    way = str(direction or DEFAULT_DIRECTION).strip().lower()
    if kind == "unknown":
        return ("unknown",
                "%r is not a sort key this check knows, so name the one your "
                "request actually sends." % (sort,))
    if way not in ("asc", "desc"):
        return ("unknown", "%r is not a direction." % (direction,))
    if kind == "mutable":
        return ("skips-and-duplicates",
                "sort=%s is a key that changes while you read, so a row can "
                "move anywhere between two requests. Both skips and duplicates "
                "are possible and only one of them is visible." % sort)
    if way == "desc":
        return ("duplicates-only",
                "sort=%s descending is stable per row, but new rows arrive at "
                "the head and shift your window, so a record can be returned "
                "twice. Nothing can be hidden." % sort)
    return ("append-only",
            "sort=%s ascending only grows at the end you have not reached yet, "
            "so this walk can neither skip a record nor return one twice." % sort)


def duplicates_within(ids):
    """Ids returned more than once inside a single walk. Pure, sorted."""
    seen, twice = set(), set()
    for i in normalize(ids):
        if i in seen:
            twice.add(i)
        seen.add(i)
    return sorted(twice)


def compare_walks(first, second):
    """Diff two walks of the same window. Pure.

    Reports the raw difference in both directions plus the repeats inside each
    walk. Interpretation is deliberately somebody else's job, because what the
    difference means depends on how the collection is sorted.
    """
    a, b = normalize(first), normalize(second)
    sa, sb = set(a), set(b)
    return {
        "missing": sorted(sa - sb),
        "appeared": sorted(sb - sa),
        "repeated": sorted(set(duplicates_within(a)) | set(duplicates_within(b))),
        "first_count": len(a),
        "second_count": len(b),
    }


def evidence(risk, diff):
    """Which parts of a two-walk diff actually prove instability. Pure.

    On an append-only walk, ids that show up only in the second pass are the
    collection growing, which is not a finding and must not be reported as one.
    On a window that shifts by design, set differences at the edges prove
    nothing either way, so only repeats count.
    """
    diff = diff or {}
    if risk == "skips-and-duplicates":
        return sorted(set(diff.get("missing") or []) | set(diff.get("appeared") or []))
    if risk == "append-only":
        return sorted(diff.get("missing") or [])
    return []


def verdict(sort, direction=None, first=None, second=None):
    """Classify the ordering, and the evidence if there is any. Pure."""
    risk, detail = walk_risk(sort, direction)
    if risk == "unknown":
        return ("unknown", detail)

    if first is not None and second is not None:
        diff = compare_walks(first, second)
        proof = evidence(risk, diff)
        if proof:
            return ("proven-skips",
                    "%d id(s) appeared in one walk of this window and not the "
                    "other, so the ordering moved between the two reads and a "
                    "record on a page boundary was never returned."
                    % len(proof))
        if diff["repeated"]:
            return ("proven-duplicates",
                    "%d id(s) came back twice inside a single walk, so the "
                    "window shifted mid read. Nothing was hidden, but the job "
                    "processed a record more than once." % len(diff["repeated"]))

    if risk == "skips-and-duplicates":
        return ("exposed",
                detail + " The two walks agreed this time, which is a quiet "
                         "window rather than a safe walk.")
    if risk == "duplicates-only":
        return ("insertion-shift", detail)
    return ("stable-walk", detail)


def stable_params(per_page=MAX_PER_PAGE, since=None):
    """The request that makes the walk safe. Pure."""
    params = {"sort": "created", "direction": "asc", "per_page": int(per_page)}
    if since:
        params["since"] = since
    return params


def repair(state):
    """The sentence a reader has to act on. Pure."""
    if state in ("proven-skips", "exposed"):
        return ("sort on an immutable key ascending, sort=created&direction=asc, "
                "so the collection only grows at the end you have not reached. "
                "For incremental work add since=<timestamp> and deduplicate on "
                "id, and for long walks use GraphQL cursors instead of offsets.")
    if state in ("proven-duplicates", "insertion-shift"):
        return ("deduplicate on id as you go, and prefer direction=asc so new "
                "rows land behind your read position instead of in front of "
                "it. Nothing is being lost here, but the same record is being "
                "processed more than once.")
    if state == "stable-walk":
        return ("nothing on the ordering. Keep per_page at 100 to reduce the "
                "number of seams, and keep the sort where it is.")
    return "name the sort and direction your request actually sends."


def read_cost(pages, walks=2):
    """Requests this run will spend against the core quota. Pure."""
    try:
        return max(0, int(pages)) * max(0, int(walks))
    except (TypeError, ValueError):
        return 0


def walk_once(session, path, params, pages):
    """Follow rel=next for at most `pages` pages, collecting ids."""
    ids, url, query = [], API + path, dict(params)
    for _ in range(max(1, pages)):
        r = session.get(url, params=query, timeout=30)
        if r.status_code == 401:
            raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, "
                             "malformed or revoked")
        if r.status_code == 403 and "rate limit" in r.text.lower():
            raise SystemExit("403 rate limited. GET /rate_limit reports the "
                             "reset time and does not itself consume quota")
        if r.status_code != 200:
            log.info("%s returned %d; stopping this walk", url, r.status_code)
            break
        try:
            items = r.json()
        except ValueError:
            break
        if not isinstance(items, list):
            break
        ids.extend(item.get("id") for item in items if isinstance(item, dict))
        nxt = parse_link(r.headers.get("Link")).get("next")
        if not nxt:
            break
        # The next URL already carries the query, so it is followed exactly as
        # given rather than rebuilt from the parameters.
        url, query = nxt, {}
    return normalize(ids)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--path", help="API path to walk, default the repo's issues")
    ap.add_argument("--sort", required=True,
                    help="the sort your request actually sends, e.g. updated")
    ap.add_argument("--direction", default=DEFAULT_DIRECTION,
                    choices=("asc", "desc"))
    ap.add_argument("--pages", type=int, default=3,
                    help="pages per walk. Two walks are made.")
    ap.add_argument("--per-page", type=int, default=MAX_PER_PAGE)
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    path = args.path or "/repos/%s/issues" % args.repo
    log.info("read cost: %d request(s) against the core hourly quota",
             read_cost(args.pages, 2))

    risk, detail = walk_risk(args.sort, args.direction)
    log.info("sort=%s direction=%s: %s", args.sort, args.direction, detail)

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })

    params = {"sort": args.sort, "direction": args.direction,
              "per_page": args.per_page}
    first = walk_once(session, path, params, args.pages)
    second = walk_once(session, path, params, args.pages)
    log.info("walk 1 collected %d id(s), walk 2 collected %d id(s)",
             len(first), len(second))

    diff = compare_walks(first, second)
    state, verdict_detail = verdict(args.sort, args.direction, first, second)
    log.info("%s: %s", state, verdict_detail)
    log.info("repair: %s", repair(state))

    print(json.dumps({
        "requests_spent": read_cost(args.pages, 2),
        "path": path,
        "sort": args.sort,
        "direction": args.direction,
        "sort_kind": sort_kind(args.sort),
        "risk": risk,
        "diff": diff,
        "evidence": evidence(risk, diff),
        "state": state,
        "detail": verdict_detail,
        "stable_params": stable_params(args.per_page),
        "repair": repair(state),
    }, indent=2, default=str))
    return 1 if state in ("proven-skips", "proven-duplicates", "exposed") else 0


if __name__ == "__main__":
    sys.exit(main())
