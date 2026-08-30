"""Find bursts of created issues and comments that will trip the content limit.

Read only. Every request is a GET, and the repair is printed rather than run.

Content-generating requests are capped at about 80 a minute and 500 an hour,
separately from the hourly quota, and no API reports how much of that allowance
is left. So this looks at the evidence a bulk writer leaves behind: the density
of created_at timestamps for a single account.
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_content_burst_audit")

API = "https://api.github.com"
UA = "github-content-burst-audit/1.0"

# Documented content-creation ceilings. Both are approximate on GitHub's side and
# neither is exposed as a bucket, which is why they are constants here.
MINUTE_LIMIT = 80
HOUR_LIMIT = 500

# A burst whose newest item is inside this many seconds of now is still running,
# which changes the advice from "pace it before next time" to "stop it".
LIVE_SECONDS = 900


def parse_ts(value):
    """ISO 8601 to epoch seconds, or None. Pure.

    GitHub always sends UTC with a trailing Z. A value that parses without a
    timezone is still treated as UTC rather than as local time, because reading
    the same log on two machines must not produce two different answers.
    """
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def peak_rate(times, window):
    """Most timestamps falling inside any window of that many seconds. Pure.

    Two pointers over a sorted list. Returns (count, ending_at) so the caller can
    say when the densest stretch was, which is what turns a number into something
    someone can go and look at.
    """
    values = sorted(t for t in (times or []) if t is not None)
    peak, at, start = 0, None, 0
    for end in range(len(values)):
        while values[end] - values[start] >= window:
            start += 1
        count = end - start + 1
        if count > peak:
            peak, at = count, values[end]
    return (peak, at)


def by_actor(items):
    """Group created_at timestamps by the login that created them. Pure.

    The limit is per account, so a repository-wide count is the wrong number:
    thirty issues in a minute from thirty people is a triage session, and thirty
    from one login is a script that is about to be throttled.
    """
    out = {}
    for item in items or []:
        user = item.get("user") or {}
        login = str(user.get("login") or "unknown")
        when = parse_ts(item.get("created_at"))
        if when is None:
            continue
        bucket = out.setdefault(login, {"times": [], "type": user.get("type") or "User"})
        bucket["times"].append(when)
    return out


def verdict(peak_minute, peak_hour, last_seen, now):
    """Classify one account's creation pattern. Pure. Returns (state, detail).

    now is a parameter rather than a call to time.time() so the same input always
    produces the same output, and so the tests can put a burst four minutes in the
    past without sleeping for four minutes.
    """
    if not peak_minute:
        return ("quiet", "nothing created in the window that was read")

    age = None if last_seen is None else max(0.0, float(now) - float(last_seen))
    when = ("still running" if age is not None and age < LIVE_SECONDS
            else "already finished" if age is not None
            else "at an unknown time")
    tail = ", %s (newest item %d minute(s) ago)" % (when, int((age or 0) // 60))

    if peak_minute >= MINUTE_LIMIT:
        return ("over-minute",
                "%d created inside one minute against a ceiling of %d. This "
                "account has already been throttled or is about to be%s"
                % (peak_minute, MINUTE_LIMIT, tail))
    if peak_hour >= HOUR_LIMIT:
        return ("over-hour",
                "%d created inside one hour against a ceiling of %d. Pacing "
                "under the per-minute limit is not enough on its own%s"
                % (peak_hour, HOUR_LIMIT, tail))
    if peak_minute >= MINUTE_LIMIT * 0.8:
        return ("near-minute",
                "%d in a minute, %d%% of the ceiling. One issue billed as two "
                "requests puts this over%s"
                % (peak_minute, int(100 * peak_minute / MINUTE_LIMIT), tail))
    if peak_hour >= HOUR_LIMIT * 0.8:
        return ("near-hour",
                "%d in an hour, %d%% of the ceiling. The per-minute rate is fine "
                "and the sustained rate is not%s"
                % (peak_hour, int(100 * peak_hour / HOUR_LIMIT), tail))
    return ("clear",
            "densest minute %d, densest hour %d, both well under %d and %d"
            % (peak_minute, peak_hour, MINUTE_LIMIT, HOUR_LIMIT))


def next_link(response):
    """The rel=next URL from the Link header, or None."""
    for part in (response.headers.get("Link") or "").split(","):
        chunk = part.strip()
        if chunk.startswith("<") and chunk.endswith('rel="next"'):
            return chunk[1:chunk.index(">")]
    return None


def get(session, url, **params):
    r = session.get(url, params=params, timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, expired or "
                         "malformed")
    if r.status_code in (403, 404):
        raise SystemExit("%d from %s: this needs read access to the repository's "
                         "issues. GitHub answers 404 rather than 403 when a "
                         "token cannot see a resource at all."
                         % (r.status_code, url))
    r.raise_for_status()
    return r


def page(session, url, limit, **params):
    out = []
    while url and len(out) < limit:
        r = get(session, url, **params)
        out.extend(r.json())
        url, params = next_link(r), {}
    return out[:limit]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--max-items", type=int, default=600,
                    help="stop paging each list after this many items")
    ap.add_argument("--actor", default=None,
                    help="only report this login (default: every author found)")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    owner, _, name = args.repo.partition("/")
    if not (owner and name):
        log.error("--repo takes owner/name, for example acme/api")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    base = "%s/repos/%s/%s" % (API, owner, name)
    items = page(session, base + "/issues", args.max_items, state="all",
                 sort="created", direction="desc", per_page=100)
    items += page(session, base + "/issues/comments", args.max_items,
                  sort="created", direction="desc", per_page=100)
    log.info("read %d issue(s), pull request(s) and comment(s) on %s",
             len(items), args.repo)

    now = datetime.now(timezone.utc).timestamp()
    findings = 0
    actors = by_actor(items)
    for login, bucket in sorted(actors.items(),
                                key=lambda kv: -len(kv[1]["times"])):
        if args.actor and login != args.actor:
            continue
        times = bucket["times"]
        peak_minute, minute_at = peak_rate(times, 60)
        peak_hour, _ = peak_rate(times, 3600)
        state, detail = verdict(peak_minute, peak_hour,
                                max(times) if times else None, now)
        line = "%s (%s): %s" % (login, bucket["type"], detail)
        if state in ("clear", "quiet"):
            log.info(line)
            continue
        findings += 1
        log.warning(line)
        if minute_at:
            log.warning("  densest minute ended at %s",
                        datetime.fromtimestamp(minute_at, timezone.utc).isoformat())
        log.warning("  repair: pace this writer to one creating request per "
                    "second and under 300 an hour, sleeping between items "
                    "rather than relying on the network being slow.")
        log.warning("  repair: on a 403 carrying retry-after, pause every "
                    "worker for that many seconds instead of retrying the one "
                    "item, and checkpoint what was created so a resume does "
                    "not duplicate it.")

    log.info("%d author(s) examined, %d over or near a content-creation ceiling",
             len(actors) if not args.actor else 1, findings)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
