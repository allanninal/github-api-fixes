"""Decide whether a polling loop should be a webhook, and cost it if it should.

Read only. Two GETs to list hooks, one to read the quota, and the repair is
printed as a command rather than run.

Detecting the client's polling behaviour from the API is a blind spot: nothing
GitHub returns says how often you call it. What is readable is the other half of
the question, which is whether a push path exists at all.
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_webhook_vs_poll")

API = "https://api.github.com"
UA = "github-webhook-vs-poll/1.0"
HOURLY_LIMIT = 5000

# The polled endpoint on the left, the event that would push the same thing on
# the right. Anything a loop reads that is not on this list is a real reason to
# keep polling, and the report says so rather than pretending otherwise.
CONCERNS = {
    "issues": ("GET /repos/{owner}/{repo}/issues", ("issues",)),
    "issue_comments": ("GET /repos/{owner}/{repo}/issues/comments", ("issue_comment",)),
    "pulls": ("GET /repos/{owner}/{repo}/pulls", ("pull_request",)),
    "commits": ("GET /repos/{owner}/{repo}/commits", ("push",)),
    "releases": ("GET /repos/{owner}/{repo}/releases", ("release",)),
    "workflow_runs": ("GET /repos/{owner}/{repo}/actions/runs", ("workflow_run",)),
}


def subscribed_events(hooks):
    """Split hook subscriptions into what delivers and what does not. Pure.

    Inactive hooks are kept separately rather than dropped. "There is a hook for
    this and it is switched off" is a thirty-second fix; "there is no hook" is a
    different job, and reporting the first as the second wastes the difference.
    """
    active, inactive = set(), set()
    wildcard = inactive_wildcard = False
    for hook in hooks or []:
        if not isinstance(hook, dict):
            continue
        live = hook.get("active") is not False
        for event in hook.get("events") or []:
            name = str(event)
            if live:
                active.add(name)
                wildcard = wildcard or name == "*"
            else:
                inactive.add(name)
                inactive_wildcard = inactive_wildcard or name == "*"
    return {"events": active, "wildcard": wildcard,
            "inactive": inactive, "inactive_wildcard": inactive_wildcard}


def coverage(concerns, hooks):
    """One row per polled concern saying whether anything would push it. Pure."""
    subs = subscribed_events(hooks)
    rows = []
    for concern in concerns or []:
        wanted = CONCERNS.get(concern, (None, (concern,)))[1]
        names = "/".join(wanted)
        if subs["wildcard"]:
            rows.append({"concern": concern, "state": "covered",
                         "detail": "a wildcard subscription delivers %s, though "
                                   "it delivers everything else too" % names})
        elif any(w in subs["events"] for w in wanted):
            rows.append({"concern": concern, "state": "covered",
                         "detail": "an active hook subscribes to %s" % names})
        elif any(w in subs["inactive"] for w in wanted) or subs["inactive_wildcard"]:
            rows.append({"concern": concern, "state": "uncovered",
                         "detail": "a hook subscribes to %s but it is not "
                                   "active, and an inactive hook delivers "
                                   "nothing" % names})
        else:
            rows.append({"concern": concern, "state": "uncovered",
                         "detail": "no hook subscribes to %s" % names})
    return rows


def poll_cost(concerns, interval_s, repos=1):
    """Requests and detection latency for the loop as configured. Pure.

    Latency is reported alongside cost because it is usually the number that
    settles the argument: the poll is both slower and more expensive than the
    push it replaces.
    """
    try:
        repos = max(0, int(repos))
    except (TypeError, ValueError):
        repos = 0
    interval = max(1, int(interval_s or 1))
    calls = len(concerns or []) * repos
    per_hour = round(calls * 3600 / interval)
    return {"requests_per_hour": per_hour,
            "requests_per_day": per_hour * 24,
            "mean_latency_s": interval / 2,
            "worst_latency_s": interval}


def verdict(rows, cost, hourly_limit=HOURLY_LIMIT):
    """Turn coverage and cost into one finding. Pure."""
    if not rows:
        return ("nothing-polled", "no concerns were named, so there is nothing "
                                  "to compare against the hooks")
    uncovered = [r for r in rows if r["state"] == "uncovered"]
    share = cost.get("requests_per_hour", 0) / max(1, hourly_limit)

    if not uncovered:
        return ("push",
                "every polled concern already has an active hook, so this loop "
                "is a reconciliation pass rather than a detection mechanism. "
                "Run it on a slow schedule.")
    summary = ("%d of %d polled concern(s) have no active hook. The loop costs "
               "%d request(s) an hour to notice them %.0fs late on average."
               % (len(uncovered), len(rows), cost.get("requests_per_hour", 0),
                  cost.get("mean_latency_s", 0)))
    if share >= 0.5:
        return ("polling-dominates",
                summary + " That is %.0f%% of the hourly quota spent on the "
                          "clock rather than on activity." % (share * 100))
    return ("polling", summary)


def get(session, path):
    """One GET. Returns (status, parsed-json-or-None)."""
    url = API + path if path.startswith("/") else path
    r = session.get(url, timeout=30)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--org", help="also read the org-level hooks (needs org admin)")
    ap.add_argument("--concerns", default="issues,issue_comments,pulls",
                    help="comma-separated list of what the loop polls for; "
                         "known names: " + ", ".join(sorted(CONCERNS)))
    ap.add_argument("--interval", type=int, default=30,
                    help="seconds between polls")
    ap.add_argument("--repos", type=int, default=1,
                    help="how many repositories the loop covers")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN. Listing hooks needs admin on the "
                  "repository, but only read access to it")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    hooks, blind = [], []
    status, body = get(session, "/repos/%s/hooks" % args.repo)
    if status == 200 and isinstance(body, list):
        hooks.extend(body)
        log.info("%s: %d repository hook(s)", args.repo, len(body))
    else:
        blind.append("repository hooks (%d)" % status)
        log.warning("could not read repository hooks: %d. This token cannot see "
                    "them, which is not the same as there being none.", status)

    if args.org:
        status, body = get(session, "/orgs/%s/hooks" % args.org)
        if status == 200 and isinstance(body, list):
            hooks.extend(body)
            log.info("%s: %d organisation hook(s)", args.org, len(body))
        else:
            blind.append("organisation hooks (%d)" % status)
            log.warning("could not read organisation hooks: %d", status)

    for hook in hooks:
        log.info("  hook %s active=%s events=%s", hook.get("id"),
                 hook.get("active"), ",".join(hook.get("events") or []) or "none")

    concerns = [c.strip() for c in args.concerns.split(",") if c.strip()]
    unknown = [c for c in concerns if c not in CONCERNS]
    for name in unknown:
        log.warning("%r is not a concern with a known event; it will be "
                    "matched against an event of the same name", name)

    rows = coverage(concerns, hooks)
    cost = poll_cost(concerns, args.interval, args.repos)
    for row in rows:
        log.info("%-16s %-10s %s", row["concern"], row["state"], row["detail"])

    status, payload = get(session, "/rate_limit")
    if status == 200:
        core = ((payload or {}).get("resources") or {}).get("core") or {}
        log.info("core quota: %s used of %s", core.get("used"), core.get("limit"))

    state, detail = verdict(rows, cost)
    log.info("%s: %s", state, detail)
    if blind:
        log.warning("unread: %s. Anything reported as uncovered may already be "
                    "covered by a hook this token cannot see.", "; ".join(blind))

    if state in ("polling", "polling-dominates"):
        needed = sorted({e for r in rows if r["state"] == "uncovered"
                         for e in CONCERNS.get(r["concern"], (None, (r["concern"],)))[1]})
        log.info("repair: create one hook for the events you consume. This "
                 "script does not create it:")
        log.info("  gh api --method POST /repos/%s/hooks -f name=web "
                 "-f config[url]=https://example.test/hooks "
                 "-f config[content_type]=json -f config[secret]=YOURSECRET %s",
                 args.repo, " ".join("-f events[]=%s" % e for e in needed))
        log.info("repair: keep the poll as reconciliation at a much longer "
                 "interval, an hour rather than %ds.", args.interval)

    print(json.dumps({"rows": rows, "cost": cost, "state": state,
                      "hooks": len(hooks), "unread": blind}, indent=2))
    return 1 if state in ("polling", "polling-dominates") else 0


if __name__ == "__main__":
    sys.exit(main())
