"""Say whether the configured repository is a fork of the one you meant.

Read only. GET requests and nothing else. There is nothing to attempt here in
any case: the failure mode of this bug is that everything succeeds, so the
finding comes from reading two repositories and comparing them rather than from
catching anything.

The point of the note: a fork is a separate repository with its own issues,
releases and branches. An integration pointed at one answers every call with a
200 and is accurate about the wrong object, so no status code, retry or alert
will ever fire.

What this can and cannot see: the API says whether a repository is a fork and
what it was forked from. It cannot say what you intended, so the verdict is
"this is a fork and here is the root of its network", and the decision to
repoint stays with you. Nor can it tell you when a name started resolving to a
different object; it can only compare the id you stored against the id now.

Environment:

    GITHUB_TOKEN    a token with read access to the repositories
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_fork_or_upstream")

API = "https://api.github.com"
UA = "github-fork-or-upstream/1.0"

# Gaps large enough that a human recognises the mistake immediately. These are
# presentation thresholds, not truth: fork=true is the finding either way.
STAR_RATIO_OBVIOUS = 10
PUSH_DAYS_OBVIOUS = 90

# GitHub timestamps are RFC 3339 in UTC with a literal Z. Python 3.9's
# fromisoformat does not accept the Z, so parse the exact shape rather than
# depending on a version difference nobody will reproduce locally.
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def read_cost(with_upstream=True, with_releases=False):
    """Requests this run will spend against the core quota. Pure."""
    cost = 1
    if with_upstream:
        cost += 1
    if with_releases:
        cost += 2 if with_upstream else 1
    return cost


def parse_ts(value):
    """One GitHub timestamp to a datetime, or None. Pure."""
    try:
        return datetime.strptime(str(value), TS_FORMAT)
    except (TypeError, ValueError):
        return None


def days_between(earlier, later):
    """Whole days from one timestamp to another, or None. Pure."""
    a, b = parse_ts(earlier), parse_ts(later)
    if a is None or b is None:
        return None
    return (b - a).days


def is_fork(repo):
    """The one boolean the whole note turns on. Pure."""
    return bool((repo or {}).get("fork"))


def upstream_of(repo):
    """The repository this one should probably have been. Pure.

    source is the root of the fork network and parent is one hop up. They
    differ for a fork of a fork, and it is source you almost always want:
    repointing at parent moves the integration one hop closer and leaves it
    wrong, which is a maddening way to fix something.
    """
    repo = repo or {}
    source = (repo.get("source") or {}).get("full_name")
    parent = (repo.get("parent") or {}).get("full_name")
    return source or parent or None


def fork_chain(repo):
    """parent and source as they were reported, for the fork-of-fork case. Pure."""
    repo = repo or {}
    return {
        "parent": (repo.get("parent") or {}).get("full_name"),
        "source": (repo.get("source") or {}).get("full_name"),
    }


def classify(repo, expected_id=None):
    """Sort the configured repository. Pure. Returns (state, detail).

    id drift is checked before the fork question because it is the case where
    nobody changed anything: a name that used to resolve to one object now
    resolves to another, and no amount of reading the configuration finds it.
    """
    repo = repo or {}
    live_id = repo.get("id")
    if expected_id not in (None, "") and live_id is not None:
        try:
            if int(expected_id) != int(live_id):
                return ("id-drift",
                        "the stored id is %s and this name now resolves to %s. "
                        "The name has moved to a different object since you "
                        "last looked, which nothing else will detect."
                        % (expected_id, live_id))
        except (TypeError, ValueError):
            pass
    if not repo:
        return ("unknown", "no repository object was read.")
    chain = fork_chain(repo)
    if is_fork(repo):
        if chain["parent"] and chain["source"] and chain["parent"] != chain["source"]:
            return ("fork-of-fork",
                    "this is a fork of %s, which is itself a fork. The root of "
                    "the network is %s and that is almost certainly the "
                    "repository you want."
                    % (chain["parent"], chain["source"]))
        return ("fork-as-canonical",
                "this repository has fork=true, so it is a separate repository "
                "with its own issues, releases and branches. Every call against "
                "it succeeds and describes it rather than %s."
                % (upstream_of(repo) or "the upstream"))
    return ("canonical",
            "fork=false, so this is a root repository and not a copy of "
            "something else.")


def divergence(fork, source):
    """The size difference between two repositories, in units people feel. Pure."""
    fork, source = fork or {}, source or {}

    def gap(key):
        a, b = fork.get(key), source.get(key)
        if a is None or b is None:
            return None
        return {"fork": a, "upstream": b, "difference": b - a}

    behind = days_between(fork.get("pushed_at"), source.get("pushed_at"))
    stars_fork = fork.get("stargazers_count") or 0
    stars_up = source.get("stargazers_count") or 0
    return {
        "stargazers_count": gap("stargazers_count"),
        "open_issues_count": gap("open_issues_count"),
        "forks_count": gap("forks_count"),
        "pushed_days_behind": behind,
        "default_branch": {"fork": fork.get("default_branch"),
                           "upstream": source.get("default_branch")},
        "obvious": bool(
            (stars_up >= STAR_RATIO_OBVIOUS * max(1, stars_fork))
            or (behind is not None and behind >= PUSH_DAYS_OBVIOUS)),
    }


def quiet_audit_reasons(repo, releases=None):
    """Why an audit of this repository would look uneventful. Pure.

    Every one of these gets blamed on something else when it arrives alone. The
    value is in seeing them gathered under one cause.
    """
    repo = repo or {}
    reasons = []
    if repo.get("has_issues") is False:
        reasons.append("issues are disabled on this fork, so issue endpoints "
                       "answer 410 rather than an empty list")
    if (repo.get("open_issues_count") or 0) == 0:
        reasons.append("no open issues")
    if releases == 0:
        reasons.append("no releases")
    if (repo.get("forks_count") or 0) == 0:
        reasons.append("nothing has forked it")
    if repo.get("archived"):
        reasons.append("the repository is archived")
    return reasons


def repair(state, repo, expected_id=None):
    """The sentence a reader has to act on. Pure."""
    upstream = upstream_of(repo)
    live_id = (repo or {}).get("id")
    if state in ("fork-as-canonical", "fork-of-fork"):
        return ("point the integration at %s and store its id beside the name, "
                "so a future rename or substitution is a mismatch rather than a "
                "quiet quarter." % (upstream or "the repository named by source"))
    if state == "id-drift":
        return ("stop trusting the name. It resolves to id %s today and your "
                "store says %s, so confirm which object you meant and rekey the "
                "state on the id." % (live_id, expected_id))
    if state == "canonical":
        return ("nothing on the fork question. Store id %s alongside the name "
                "anyway; it is the only key that survives a rename."
                % (live_id,))
    return "read the repository first; there is nothing to judge yet."


def get(session, path):
    """One GET. Returns the response object."""
    r = session.get(API + path, timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed "
                         "or revoked")
    return r


def release_count(session, full_name):
    """0, 1-or-more, or None if it could not be read. Cheap on purpose."""
    r = get(session, "/repos/%s/releases?per_page=1" % full_name)
    if r.status_code != 200:
        return None
    try:
        return len(r.json())
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo", help="owner/name the integration is configured with")
    ap.add_argument("--expect-id", default="",
                    help="the repository id your state store recorded")
    ap.add_argument("--no-upstream", action="store_true",
                    help="skip the second read of the upstream repository")
    ap.add_argument("--releases", action="store_true",
                    help="also check whether either repository has releases")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2
    if "/" not in args.repo:
        log.error("repo should be owner/name")
        return 2

    log.info("read cost: %d request(s) against the core hourly quota",
             read_cost(not args.no_upstream, args.releases))

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })

    response = get(session, "/repos/" + args.repo)
    if response.status_code != 200:
        log.error("%s: HTTP %s reading the repository", args.repo,
                  response.status_code)
        return 2
    repo = response.json()
    chain = fork_chain(repo)
    log.info("%s: fork=%s id=%s pushed_at=%s", args.repo, repo.get("fork"),
             repo.get("id"), repo.get("pushed_at"))
    log.info("  parent=%s source=%s", chain["parent"], chain["source"])

    state, detail = classify(repo, args.expect_id or None)
    log.info("%s: %s", state, detail)

    gaps = None
    upstream = upstream_of(repo)
    if upstream and not args.no_upstream:
        up = get(session, "/repos/" + upstream)
        if up.status_code == 200:
            gaps = divergence(repo, up.json())
            log.info("gap against %s: stars %s vs %s, open issues %s vs %s, "
                     "forks %s vs %s, last push %s day(s) behind", upstream,
                     (gaps["stargazers_count"] or {}).get("fork"),
                     (gaps["stargazers_count"] or {}).get("upstream"),
                     (gaps["open_issues_count"] or {}).get("fork"),
                     (gaps["open_issues_count"] or {}).get("upstream"),
                     (gaps["forks_count"] or {}).get("fork"),
                     (gaps["forks_count"] or {}).get("upstream"),
                     gaps["pushed_days_behind"])
        else:
            log.warning("could not read %s: HTTP %s", upstream, up.status_code)

    releases = release_count(session, args.repo) if args.releases else None
    reasons = quiet_audit_reasons(repo, releases)
    if reasons:
        log.info("quiet-audit-explained: %s", "; ".join(reasons))
    log.info("repair: %s", repair(state, repo, args.expect_id or None))

    print(json.dumps({
        "configured": args.repo,
        "id": repo.get("id"),
        "node_id": repo.get("node_id"),
        "fork": repo.get("fork"),
        "chain": chain,
        "upstream": upstream,
        "state": state,
        "detail": detail,
        "divergence": gaps,
        "quiet_audit_reasons": reasons,
        "repair": repair(state, repo, args.expect_id or None),
    }, indent=2, default=str))
    return 1 if state in ("fork-as-canonical", "fork-of-fork", "id-drift") else 0


if __name__ == "__main__":
    sys.exit(main())
