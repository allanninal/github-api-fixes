"""Find archived repositories before a write loop discovers them the hard way.

Read only. One GET per repository, or one per hundred in an organisation
sweep, and nothing is written. In particular no write is attempted against an
archived repository to confirm the 403: the archived boolean on the repository
object is the finding, it arrives before any write would be sent, and the
repository was deliberately frozen by somebody who would rather it stayed that
way.

The point of the note: archiving makes a repository read-only. Reads keep
working, which is why the failure looks selective and looks like permissions,
and every write is refused with 403 regardless of the token, the scopes, the
App permissions or the caller's role. No credential change fixes it.

What this can and cannot see: whether your own client retries a 403 is
invisible from here, so the retry cost is computed from a rate you supply. The
archived boolean itself is exact.

Environment:

    GITHUB_TOKEN    a token with read access to the repositories
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_archived_repo_guard")

API = "https://api.github.com"
UA = "github-archived-repo-guard/1.0"

# The core hourly quota a retrying client is spending these requests out of.
CORE_QUOTA_PER_HOUR = 5000

# One listing request covers this many repositories.
ORG_PAGE_SIZE = 100

# Words that identify an archived repository in a refusal you already recorded.
ARCHIVED_WORDS = ("archived", "read-only", "read only")


def lifecycle(repo):
    """Which platform state this repository is in. Pure.

    Four states rather than two: archived and disabled are separate booleans
    that arrive in the same response, they can both be set, and they have
    different owners and different remedies.
    """
    if not isinstance(repo, dict):
        return "unknown"
    archived = bool(repo.get("archived"))
    disabled = bool(repo.get("disabled"))
    if archived and disabled:
        return "archived-and-disabled"
    if archived:
        return "archived"
    if disabled:
        return "disabled"
    return "active"


def accepts_writes(state):
    """Whether a write to this repository can ever be accepted. Pure."""
    if state in ("archived", "disabled", "archived-and-disabled"):
        return False
    if state == "active":
        return True
    return None


def retry_policy(state):
    """What a client should do with a failure here. Pure.

    This is the output that belongs in the write path. A 403 from a rate limit
    is worth retrying and a 403 from an archived repository never is, and the
    status code cannot tell them apart.
    """
    if accepts_writes(state) is False:
        return "permanent-skip"
    if state == "active":
        return "retry"
    return "unknown"


def explain(state):
    """Why this repository refuses writes, in one sentence. Pure."""
    if state == "archived":
        return ("archiving makes a repository read-only, so no write will ever "
                "be accepted here regardless of the token.")
    if state == "disabled":
        return ("the repository is disabled, which is a different state with a "
                "different owner: see the disabled repository note.")
    if state == "archived-and-disabled":
        return ("the repository is both archived and disabled. Unarchiving it "
                "would still leave it disabled, so the disabled state is the "
                "one to resolve first.")
    if state == "active":
        return "the repository accepts writes; this refusal is about something else."
    return "the repository could not be read, so its state is unknown."


def classify_failure(status, message):
    """Attribute a refusal you already recorded. Pure. (state, detail).

    Nothing is sent to produce this. The message comes out of your logs, and
    the repository object has already answered the same question independently.
    """
    text = str(message or "").lower()
    try:
        code = int(status)
    except (TypeError, ValueError):
        code = None

    if any(word in text for word in ARCHIVED_WORDS):
        return ("archived-refusal",
                "the message names the repository as archived, which is a "
                "property of the repository and not of your credential.")
    if "rate limit" in text:
        return ("rate-limited",
                "a rate limit, which is a transient 403 and the one kind worth "
                "retrying. That is a different note.")
    if "not accessible" in text or "integration" in text or "personal access token" in text:
        return ("credential-refusal",
                "the message blames the credential rather than the repository, "
                "so this is a permissions problem and widening the grant may "
                "actually help.")
    if code == 404:
        return ("not-found",
                "404 rather than 403, which means several things at once and "
                "needs its own triage.")
    if code == 403:
        return ("forbidden-unattributed",
                "a 403 whose message names neither the repository state nor a "
                "rate limit. Read the repository object to settle it.")
    return ("no-failure", "nothing here names a refusal.")


def days_since(timestamp, now=None):
    """Whole days between an ISO 8601 timestamp and now. Pure. None if absent."""
    if not timestamp:
        return None
    text = str(timestamp).replace("Z", "+00:00")
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0, (now - when).days)


def wasted_requests(attempts_per_hour, repositories, hours=1):
    """Requests a retrying client spends on refusals that cannot succeed. Pure."""
    try:
        rate = max(0, int(attempts_per_hour or 0))
        count = max(0, int(repositories or 0))
        span = max(0, int(hours or 0))
    except (TypeError, ValueError):
        return 0
    return rate * count * span


def quota_share(requests_per_hour, quota=CORE_QUOTA_PER_HOUR):
    """That spend as a whole-number percentage of the hourly quota. Pure."""
    try:
        spend = max(0, int(requests_per_hour or 0))
    except (TypeError, ValueError):
        return 0
    if not quota:
        return 0
    return int(round(100.0 * spend / quota))


def skip_list(rows):
    """The repositories a write loop should never visit. Pure and sorted."""
    names = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if accepts_writes(row.get("state")) is False and row.get("full_name"):
            names.append(str(row["full_name"]))
    return sorted(set(names))


def summarise(rows):
    """Counts for the bottom of the report. Pure."""
    counts = {"total": 0, "archived": 0, "disabled": 0, "writable": 0,
              "unknown": 0}
    for row in rows or []:
        state = (row or {}).get("state")
        counts["total"] += 1
        if state in ("archived", "archived-and-disabled"):
            counts["archived"] += 1
        if state in ("disabled", "archived-and-disabled"):
            counts["disabled"] += 1
        if state == "active":
            counts["writable"] += 1
        if state == "unknown":
            counts["unknown"] += 1
    return counts


def repair(state):
    """The sentence a reader has to act on. Pure."""
    if state == "archived":
        return ("filter archived repositories out at the top of the write loop "
                "and treat this as a permanent skip. Unarchive only if the "
                "repository is genuinely still in use.")
    if state == "disabled":
        return ("see /github/repo-disabled/ -- a disabled repository is a "
                "different state with a different owner, usually billing or a "
                "terms problem rather than a decision on your side.")
    if state == "archived-and-disabled":
        return ("resolve the disabled state with GitHub first; unarchiving on "
                "its own will not make this repository writable.")
    if state == "active":
        return ("nothing here. This repository accepts writes, so a refusal "
                "against it is about the credential or the branch.")
    if state == "archived-refusal":
        return ("stop retrying and skip. No token, scope or App permission "
                "makes an archived repository writable.")
    if state == "rate-limited":
        return ("honour retry-after and slow down. This one really is worth "
                "retrying.")
    if state == "credential-refusal":
        return ("triage the credential: the message blames the token or the "
                "integration rather than the repository state.")
    return ("read the repository object and use the archived and disabled "
            "booleans rather than inferring state from a status code.")


def read_cost_for_repos(repos):
    """Requests a per-repository run will spend. Pure."""
    return len(repos or [])


def pages_for(count, page_size=ORG_PAGE_SIZE):
    """Listing requests an organisation of this size needs. Pure."""
    try:
        total = max(0, int(count or 0))
    except (TypeError, ValueError):
        return 0
    if not total:
        return 0
    return (total + page_size - 1) // page_size


def parse_link(header):
    """The Link header as {rel: url}. Pure.

    Scanned rather than split on commas, because a URL may contain one and a
    naive split turns the next page into two unusable halves.
    """
    text = str(header or "")
    links, i = {}, 0
    while True:
        start = text.find("<", i)
        if start < 0:
            break
        end = text.find(">", start)
        if end < 0:
            break
        url = text[start + 1:end]
        tail = text[end + 1:]
        stop = tail.find("<")
        segment = tail if stop < 0 else tail[:stop]
        rel = ""
        for bit in segment.split(";"):
            bit = bit.strip()
            if bit.startswith("rel="):
                rel = bit[4:].strip().strip(",").strip('"')
        if rel:
            links[rel] = url
        i = end + 1
    return links


def row_for(repo):
    """One report row from one repository object. Pure."""
    state = lifecycle(repo)
    return {
        "full_name": (repo or {}).get("full_name"),
        "state": state,
        "accepts_writes": accepts_writes(state),
        "retry_policy": retry_policy(state),
        "explanation": explain(state),
        "days_since_last_push": days_since((repo or {}).get("pushed_at")),
        "repair": repair(state),
    }


def get_repo(session, full_name):
    """One GET of a repository object. Returns a dict or None."""
    r = session.get(API + "/repos/" + full_name, timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed "
                         "or revoked")
    if r.status_code == 403 and "rate limit" in r.text.lower():
        raise SystemExit("403 rate limited. GET /rate_limit reports the reset "
                         "time and does not itself consume quota")
    if r.status_code != 200:
        return None
    try:
        body = r.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def list_org_repos(session, org, max_pages=20):
    """Every repository in an organisation. Returns (repos, requests_spent)."""
    url = "%s/orgs/%s/repos?type=all&per_page=%d" % (API, org, ORG_PAGE_SIZE)
    repos, spent = [], 0
    while url and spent < max_pages:
        r = session.get(url, timeout=30)
        spent += 1
        if r.status_code != 200:
            break
        try:
            page = r.json()
        except ValueError:
            break
        if not isinstance(page, list):
            break
        repos.extend(item for item in page if isinstance(item, dict))
        url = parse_link(r.headers.get("Link")).get("next")
    return repos, spent


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", action="append", default=[],
                    help="owner/name to check. Repeatable.")
    ap.add_argument("--org", help="sweep every repository in an organisation")
    ap.add_argument("--attempts-per-hour", type=int, default=0,
                    help="how often your write loop retries a failing "
                         "repository, so the waste can be stated in requests")
    ap.add_argument("--failure-message", default="",
                    help="a refusal you already recorded, to attribute it "
                         "without reproducing it")
    ap.add_argument("--failure-status", default="",
                    help="the status code recorded alongside it")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2
    if not args.repo and not args.org:
        log.error("give at least one --repo or an --org to sweep")
        return 2

    if args.repo:
        log.info("read cost: %d request(s) against the core hourly quota",
                 read_cost_for_repos(args.repo))
    if args.org:
        log.info("read cost: 1 request(s) per %d repositories in an org sweep",
                 ORG_PAGE_SIZE)

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })

    rows = []
    if args.org:
        repos, spent = list_org_repos(session, args.org)
        log.info("%s: %d repository(ies) read in %d request(s)", args.org,
                 len(repos), spent)
        rows.extend(row_for(repo) for repo in repos)
    for name in args.repo:
        repo = get_repo(session, name)
        if repo is None:
            rows.append({"full_name": name, "state": "unknown",
                         "accepts_writes": None, "retry_policy": "unknown",
                         "explanation": explain("unknown"),
                         "days_since_last_push": None,
                         "repair": repair("unknown")})
        else:
            rows.append(row_for(repo))

    frozen = [row for row in rows if row["accepts_writes"] is False]
    for row in frozen:
        log.info("%s: %s", row["full_name"], row["state"])
        log.info("  %s: %s", row["retry_policy"], row["explanation"])
        if row["days_since_last_push"] is not None:
            log.info("  last push %d day(s) ago", row["days_since_last_push"])
        log.info("  repair: %s", row["repair"])

    recorded = None
    if args.failure_message or args.failure_status:
        state, detail = classify_failure(args.failure_status, args.failure_message)
        log.info("recorded failure -> %s: %s", state, detail)
        log.info("repair: %s", repair(state))
        recorded = {"state": state, "detail": detail}

    spend = wasted_requests(args.attempts_per_hour, len(frozen))
    if spend:
        log.info("retry cost: %d attempt(s)/hour against %d frozen "
                 "repository(ies) is %d request(s)/hour, %d a day, %d%% of a "
                 "%d/hour quota", args.attempts_per_hour, len(frozen), spend,
                 spend * 24, quota_share(spend), CORE_QUOTA_PER_HOUR)

    counts = summarise(rows)
    log.info("summary: %d repositories, %d archived, %d disabled, %d writable",
             counts["total"], counts["archived"], counts["disabled"],
             counts["writable"])

    print(json.dumps({
        "counts": counts,
        "skip_list": skip_list(rows),
        "wasted_requests_per_hour": spend,
        "quota_share_percent": quota_share(spend),
        "recorded_failure": recorded,
        "repositories": rows,
    }, indent=2, default=str))
    return 1 if frozen else 0


if __name__ == "__main__":
    sys.exit(main())
