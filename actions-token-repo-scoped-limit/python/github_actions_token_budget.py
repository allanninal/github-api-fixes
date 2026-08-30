"""Cost a workflow against the request pool it shares with its own repository.

Read only. Every request is a GET. GET /rate_limit consumes no quota from any
bucket, and the optional identity probe is a single GET /user used for its
status code rather than its body.

The built-in Actions credential is not a small personal access token. It is a
different class with a 1,000 an hour core ceiling, and that ceiling belongs to
the repository rather than to the job, so every concurrent job and every matrix
leg in the run draws from the same pool on the same clock.
"""
import argparse
import json
import logging
import os
import sys
import time

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_actions_token_budget")

API = "https://api.github.com"
UA = "github-actions-token-budget/1.0"

# Ceilings that identify a credential class outright. 5000 is deliberately not
# in here: it is both the authenticated-user allowance and the floor for a
# GitHub App installation, so on its own it names two things and settles none.
ACTIONS_CEILING = 1000
ANON_CEILING = 60
ENTERPRISE_CEILING = 15000
USER_CEILING = 5000


def classify(core_limit, graphql_limit=None, user_status=None):
    """Name the credential class from the ceilings it was handed. Pure.

    Returns (klass, confidence, note). Confidence matters here because one of
    the numbers is genuinely ambiguous and pretending otherwise sends people
    looking for an App installation they do not have.
    """
    try:
        core = int(core_limit)
    except (TypeError, ValueError):
        return ("unknown", "none",
                "GET /rate_limit reported no core limit, so there is no ceiling "
                "to cost anything against")

    if core <= 0:
        return ("unknown", "none", "a core limit of %d is not a ceiling" % core)
    if core <= ANON_CEILING:
        return ("anonymous", "high",
                "a core ceiling of %d is the anonymous tier, counted per "
                "originating IP address. No credential is reaching GitHub" % core)
    if core == ACTIONS_CEILING:
        seconds = []
        if user_status == 403:
            seconds.append("GET /user answered 403, which a user token never does")
        try:
            if int(graphql_limit) == ACTIONS_CEILING:
                seconds.append("the graphql row is 1000 points as well")
        except (TypeError, ValueError):
            pass
        note = ("a core ceiling of 1000 an hour is the built-in Actions token, "
                "and it belongs to the repository rather than to this job")
        if seconds:
            note += ". " + "; ".join(seconds)
        return ("actions-token", "high" if seconds else "likely", note)
    if core == ENTERPRISE_CEILING:
        return ("enterprise-user", "likely",
                "15000 an hour is a user on GitHub Enterprise Cloud")
    if core == USER_CEILING:
        return ("user-or-app", "ambiguous",
                "5000 an hour is an authenticated user token or a GitHub App "
                "installation still at the floor; the number names two things "
                "and settles neither")
    if core > USER_CEILING:
        return ("app-installation", "likely",
                "%d an hour is above the 5000 floor, which only a GitHub App "
                "installation scaled by repositories and users reaches" % core)
    return ("unknown", "none",
            "a core ceiling of %d does not match a documented class" % core)


def plan(jobs, calls_per_job, matrix_legs=1, ceiling=ACTIONS_CEILING, remaining=None):
    """What one workflow run costs against a pool the repository shares. Pure.

    `remaining` is the honest input and `ceiling` is the optimistic one: the
    limit is what you would have had at the top of the hour, and remaining is
    what the rest of the repository left you. The source is reported so a reader
    can tell which number the verdict was built on.
    """
    def whole(value, floor=0):
        try:
            return max(floor, int(value))
        except (TypeError, ValueError):
            return floor

    legs = max(1, whole(matrix_legs, 1))
    count = whole(jobs)
    calls = whole(calls_per_job)
    ceiling = max(1, whole(ceiling, 1))

    effective = count * legs
    total = effective * calls
    if remaining is None:
        headroom, source = ceiling, "limit"
    else:
        headroom, source = whole(remaining), "remaining"

    served = effective if not calls else min(effective, headroom // calls)
    return {"legs": legs, "jobs": effective, "calls_per_job": calls,
            "total": total, "headroom": headroom, "source": source,
            "fits": total <= headroom, "jobs_served": served,
            "first_starved_job": None if total <= headroom else served + 1,
            "shortfall": max(0, total - headroom)}


def pool_reset_in(reset, now):
    """Seconds until the shared pool refills, floored at zero. Pure.

    None rather than 0 when the value is unreadable, because "refills now" and
    "I could not read the reset" must not print the same.
    """
    try:
        return max(0, int(reset) - int(now))
    except (TypeError, ValueError):
        return None


def verdict(klass, costing):
    """Turn the class and the costing into a finding. Pure."""
    if klass == "anonymous":
        return ("unauthenticated",
                "the ceiling being costed is the anonymous 60 an hour, so this "
                "is not a workflow budget problem yet: no credential is "
                "arriving at GitHub.")
    if costing["total"] == 0:
        return ("no-workflow",
                "no workflow was described, so there is nothing to cost against "
                "the %d request pool." % costing["headroom"])
    if klass != "actions-token":
        return ("different-ceiling",
                "the credential in this environment reads as %s with a ceiling "
                "of %d, not the 1000 the Actions token gets. The %d request(s) "
                "this run makes fit here and will not fit there. Run the check "
                "from inside the job." % (klass, costing["headroom"], costing["total"]))
    if not costing["fits"]:
        return ("pool-overrun",
                "%d job(s) at %d call(s) each is %d request(s) against a pool "
                "of %d that the whole repository shares. Job %d of %d is the "
                "first to start seeing 403, and any other run in the same hour "
                "moves that number down."
                % (costing["jobs"], costing["calls_per_job"], costing["total"],
                   costing["headroom"], costing["first_starved_job"], costing["jobs"]))
    if costing["total"] * 5 >= costing["headroom"] * 4:
        return ("pool-tight",
                "%d request(s) against %d is over four fifths of a pool shared "
                "with every other job and every other run in this repository "
                "within the same hour."
                % (costing["total"], costing["headroom"]))
    return ("fits",
            "%d request(s) against a shared pool of %d."
            % (costing["total"], costing["headroom"]))


def get(session, path):
    """One GET. Returns (status, json-or-None)."""
    response = session.get(API + path, timeout=30)
    try:
        body = response.json()
    except ValueError:
        body = None
    return response.status_code, body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=0,
                        help="jobs in the workflow run")
    parser.add_argument("--calls", type=int, default=0,
                        help="core API requests one job makes")
    parser.add_argument("--matrix", type=int, default=1,
                        help="matrix legs each job expands into")
    parser.add_argument("--env", default="GITHUB_TOKEN",
                        help="environment variable holding the credential")
    parser.add_argument("--use-limit", action="store_true",
                        help="cost against the hourly limit rather than what "
                             "the repository has left right now")
    args = parser.parse_args()

    token = os.environ.get(args.env)
    if not token:
        log.error("set %s. Inside a workflow this is the credential Actions "
                  "injects; on a laptop it is your own, and the whole point of "
                  "this check is that the two have different ceilings", args.env)
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    status, payload = get(session, "/rate_limit")
    if status != 200:
        log.error("GET /rate_limit returned %d; without it there is no ceiling "
                  "to reason about", status)
        return 2

    resources = ((payload or {}).get("resources") or {})
    core = resources.get("core") or {}
    graphql = resources.get("graphql") or {}

    user_status, _ = get(session, "/user")
    log.info("GET /user answered %d (used as a fingerprint, not for its body)",
             user_status)

    klass, confidence, note = classify(core.get("limit"), graphql.get("limit"),
                                       user_status)
    log.info("%s (%s): %s", klass, confidence, note)
    log.info("core limit %s remaining %s, graphql limit %s remaining %s",
             core.get("limit"), core.get("remaining"),
             graphql.get("limit"), graphql.get("remaining"))

    wait = pool_reset_in(core.get("reset"), time.time())
    if wait is not None:
        log.info("the shared pool refills in %ds", wait)

    if os.environ.get("GITHUB_ACTIONS") != "true":
        log.warning("GITHUB_ACTIONS is not set to true, so this is not running "
                    "inside a workflow and the ceiling below is your laptop's, "
                    "not the one the job will get")

    ceiling = core.get("limit") or ACTIONS_CEILING
    remaining = None if args.use_limit else core.get("remaining")
    costing = plan(args.jobs, args.calls, args.matrix, ceiling, remaining)
    log.info("costed against the %s: %d request(s) against %d",
             costing["source"], costing["total"], costing["headroom"])

    state, detail = verdict(klass, costing)
    log.info("%s: %s", state, detail)

    if state in ("pool-overrun", "pool-tight"):
        log.info("repair: collapse related REST reads into one GraphQL query, "
                 "and send If-None-Match on repeats. A 304 does not count "
                 "against the primary limit.")
        log.info("repair: for volume that cannot be reduced, authenticate as a "
                 "GitHub App installation instead of the built-in token. That "
                 "lifts the floor to 5000 and scales past it.")
        log.info("repair: reduce concurrency. The pool is per repository, so "
                 "matrix legs do not each get their own budget.")

    print(json.dumps({"class": klass, "confidence": confidence,
                      "plan": costing, "state": state}, indent=2))
    return 1 if state in ("pool-overrun", "pool-tight", "unauthenticated") else 0


if __name__ == "__main__":
    sys.exit(main())
