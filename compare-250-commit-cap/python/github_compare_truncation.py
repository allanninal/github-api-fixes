"""Report whether a compare response was silently truncated at 250 commits.

Read only. One GET, no writes: a token with read access to the repository is
enough. The repair is printed, never performed.

The request is deliberately made without per_page or page, because that is the
call whose 250-commit cap is invisible, and reproducing it is the only way to
measure what an unpaginated client is missing.
"""
import argparse
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_compare_truncation")

API = "https://api.github.com"

CAP = 250


def verdict(compare):
    """Classify one compare response. Pure. Returns (state, detail).

    `compare` is the parsed JSON: total_commits, commits and files.

    A missing total_commits is its own state rather than a default of zero.
    Defaulting it would report a truncated comparison as complete, which is the
    exact failure this script exists to catch.
    """
    total = compare.get("total_commits")
    if total is None:
        return ("unknown",
                "no total_commits in the response, so completeness cannot be "
                "judged. Do not treat this as complete.")

    total = int(total)
    commits = compare.get("commits") or []
    received = len(commits)
    files = len(compare.get("files") or [])

    if total == 0:
        return ("empty", "no commits between these refs; head is not ahead of base")

    if received >= total:
        return ("complete",
                "%d commit(s), all present%s."
                % (total, " (%d changed file(s))" % files if files else ""))

    if received == CAP:
        return ("capped",
                "total_commits is %d and %d came back: the unpaginated 250-commit "
                "cap, so %d commit(s) are missing. The last entry in this list is "
                "the head of the comparison, not the 250th commit from the base, "
                "so the array is not a contiguous history."
                % (total, received, total - received))

    return ("truncated",
            "total_commits is %d and %d came back, so %d commit(s) are missing. "
            "This is what a paginated read looks like mid-walk; keep paging until "
            "the counts agree." % (total, received, total - received))


def get(session, path, **params):
    r = session.get(API + path, params=params, timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed or "
                         "revoked")
    if r.status_code == 403 and "rate limit" in r.text.lower():
        raise SystemExit("403 rate limited. GET /rate_limit reports the reset time "
                         "and does not itself consume quota")
    if r.status_code == 404:
        raise SystemExit("404 on %s: check the repository and that both refs exist" % path)
    r.raise_for_status()
    return r.json()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--base", required=True, help="base ref, tag or sha")
    ap.add_argument("--head", required=True, help="head ref, tag or sha")
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
        "User-Agent": "github-compare-truncation",
    })

    # No per_page and no page on purpose: this is the call the cap applies to.
    path = "/repos/%s/compare/%s...%s" % (args.repo, args.base, args.head)
    body = get(session, path)
    state, detail = verdict(body)

    line = "%-10s %s...%s  %s" % (state, args.base, args.head, detail)
    if state in ("complete", "empty"):
        log.info(line)
        return 0

    log.warning(line)
    log.warning("  repair: read total_commits first, then page this endpoint with "
                "per_page=100 and page=N until you have that many commits, keeping "
                "files from the first page only. Or read "
                "/repos/%s/commits?sha=%s, which paginates through the Link "
                "header and has no 250-commit ceiling.", args.repo, args.head)
    return 1


if __name__ == "__main__":
    sys.exit(main())
