"""Say whether an endpoint refused you because the feature is switched off.

Read only. GET requests and nothing else. Nothing here is established by
attempting anything: the repository object carries every feature flag, and the
optional probe is the same GET the failing job already makes.

The point of the note: some endpoints are gated on a repository feature as well
as on a permission. When the feature is off they refuse everybody, including a
caller holding exactly the permission the endpoint names, and one off switch
produces three different status codes depending on the endpoint family.

What this can and cannot see: security_and_analysis is only returned to a
caller with admin on the repository. An absent block is therefore unreported,
not disabled, and the script keeps those apart. Some flags gate their endpoint
exactly and one is the closest available proxy; the table says which.

Environment:

    GITHUB_TOKEN    a token with read access to the repository
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_feature_flags")

API = "https://api.github.com"
UA = "github-feature-flags/1.0"

# Read out of security_and_analysis, which is a map of {name: {status: ...}}.
SECURITY_FEATURES = (
    "advanced_security",
    "secret_scanning",
    "secret_scanning_push_protection",
    "secret_scanning_non_provider_patterns",
    "dependabot_security_updates",
)

# Plain booleans on the repository object itself. These come back to any caller
# who can read the repository, unlike the security block.
TOGGLES = ("has_issues", "has_wiki", "has_projects", "has_discussions",
           "has_pages", "has_downloads")

# The table this note exists for. Each row: which flag gates the endpoint, where
# that flag is read from, the status a disabled feature produces there, and
# whether the mapping is exact or the closest available proxy.
#
# The three status codes are the whole trap. Only the 403 looks like a
# permissions problem, and all three mean the feature is off.
ENDPOINT_FEATURES = {
    "/code-scanning/alerts": ("advanced_security", "security", 403, "exact"),
    "/code-scanning/analyses": ("advanced_security", "security", 403, "exact"),
    "/secret-scanning/alerts": ("secret_scanning", "security", 404, "exact"),
    "/dependabot/alerts": ("dependabot_security_updates", "security", 403, "proxy"),
    "/issues": ("has_issues", "toggle", 410, "exact"),
    "/issues/comments": ("has_issues", "toggle", 410, "exact"),
    "/milestones": ("has_issues", "toggle", 410, "exact"),
}

# Why the proxy rows are not presented as certainties.
PROXY_NOTE = ("this flag is the closest one the repository object publishes for "
              "that endpoint rather than a switch for it exactly, so a disabled "
              "reading here is strong evidence and not proof.")

# Advanced Security on a private or internal repository depends on the plan as
# well as on the checkbox, which is a repair a repository admin cannot make.
PLAN_DEPENDENT = ("advanced_security", "secret_scanning",
                  "secret_scanning_push_protection")


def read_cost(probes=0):
    """Requests this run will spend against the core quota. Pure."""
    return 1 + max(0, int(probes or 0))


def normalise_endpoint(path):
    """Reduce a logged URL to a key in the table. Pure.

    Accepts a full URL, a /repos/{owner}/{repo}/... path or the bare suffix, so
    a line can be pasted straight out of a log without editing.
    """
    text = str(path or "").strip()
    for prefix in ("https://api.github.com", "http://api.github.com"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = text.split("?")[0].rstrip("/")
    if not text:
        return ""
    if not text.startswith("/"):
        text = "/" + text
    if text.startswith("/repos/"):
        parts = text.split("/")
        # /repos/{owner}/{repo}/rest -> /rest
        if len(parts) > 4:
            text = "/" + "/".join(parts[4:])
        else:
            text = "/"
    return text


def feature_for(path):
    """The table row for an endpoint, or None. Pure."""
    key = normalise_endpoint(path)
    row = ENDPOINT_FEATURES.get(key)
    if row is None:
        return None
    feature, source, status, confidence = row
    return {"endpoint": key, "feature": feature, "source": source,
            "status_when_disabled": status, "confidence": confidence}


def security_block(repo):
    """The security_and_analysis map, or None when it was not returned. Pure."""
    block = (repo or {}).get("security_and_analysis")
    return block if isinstance(block, dict) else None


def flag_state(repo, feature, source):
    """enabled, disabled or unreported for one feature. Pure.

    unreported is a real third answer. The security block is only sent to a
    caller with admin on the repository, so its absence describes the reader
    rather than the repository, and calling that "disabled" would be a
    confident claim built on a missing grant.
    """
    if source == "toggle":
        value = (repo or {}).get(feature)
        if value is True:
            return "enabled"
        if value is False:
            return "disabled"
        return "unreported"
    block = security_block(repo)
    if block is None:
        return "unreported"
    entry = block.get(feature)
    if not isinstance(entry, dict):
        return "unreported"
    status = str(entry.get("status") or "").strip().lower()
    if status in ("enabled", "disabled"):
        return status
    return "unreported"


def matrix(repo):
    """Every endpoint in the table with the state of the flag gating it. Pure."""
    rows = []
    for key in sorted(ENDPOINT_FEATURES):
        row = feature_for(key)
        row["state"] = flag_state(repo, row["feature"], row["source"])
        row["will_serve"] = {"enabled": True, "disabled": False}.get(row["state"])
        rows.append(row)
    return rows


def plan_may_be_the_constraint(repo, feature):
    """Is this a repair a repository admin might not be able to make. Pure."""
    if feature not in PLAN_DEPENDENT:
        return False
    visibility = str((repo or {}).get("visibility") or "").strip().lower()
    return bool((repo or {}).get("private")) or visibility in ("private", "internal")


def status_matches(row, observed):
    """Does the recorded status match what a disabled feature produces. Pure.

    Returns True, False, or None when nothing was recorded. A mismatch is worth
    saying out loud: it means the refusal probably has a different cause, and
    forcing it into the story here would be the same mistake in a new direction.
    """
    if observed in (None, ""):
        return None
    try:
        return int(observed) == int(row["status_when_disabled"])
    except (TypeError, ValueError):
        return None


def classify(repo, row, observed_status=None, accepted_permissions=None):
    """Attribute one refusal to the switch or to the grant. Pure.

    Returns (state, detail).
    """
    if row is None:
        return ("endpoint-unknown",
                "that endpoint is not one of the feature-gated ones in this "
                "table, so a refusal from it is not this note. Read the whole "
                "flag matrix above and check the permission headers instead.")
    state_of_flag = row["state"] if "state" in row else flag_state(
        repo, row["feature"], row["source"])
    named = str(accepted_permissions or "").strip()
    match = status_matches(row, observed_status)

    if state_of_flag == "unreported":
        return ("feature-unreported",
                "%s could not be read. The security_and_analysis block is only "
                "returned to a caller with admin on the repository, so this "
                "says something about your own role rather than about the "
                "feature." % row["feature"])
    if state_of_flag == "disabled":
        if match is False:
            return ("status-mismatch",
                    "%s is disabled, but a disabled feature answers %s on this "
                    "endpoint and you recorded %s. Fix the feature and expect "
                    "the other failure to survive it."
                    % (row["feature"], row["status_when_disabled"], observed_status))
        return ("feature-disabled",
                "%s is disabled on this repository, and %s is what a disabled "
                "feature produces here. No permission opens it."
                % (row["feature"], row["status_when_disabled"]))
    if named:
        return ("permission-named",
                "%s is enabled and the response named '%s' in "
                "x-accepted-github-permissions, so this is a grant that is "
                "missing rather than a feature that is off."
                % (row["feature"], named))
    return ("feature-enabled",
            "%s is enabled, so the feature is not what refused you. Look at the "
            "credential next: a fine-grained token names no permission on its "
            "own refusal, and an App names one in a header." % row["feature"])


def repair(state, row, repo=None):
    """The sentence a reader has to act on. Pure."""
    feature = (row or {}).get("feature", "the feature")
    if state == "feature-disabled":
        text = ("enable %s on this repository in its security settings, or at "
                "organization level for every repository, then grant the "
                "caller the matching read permission. Both, in that order."
                % feature)
        if plan_may_be_the_constraint(repo or {}, feature):
            text += (" This is a private or internal repository, so "
                     "availability depends on the plan as well as on the "
                     "checkbox, and that part is not a repository setting.")
        if (row or {}).get("confidence") == "proxy":
            text += " Note that " + PROXY_NOTE
        return text
    if state == "feature-unreported":
        return ("read the repository with an account that has admin on it, or "
                "ask an admin what the setting says. Do not record this as "
                "disabled: an absent block is a limit on your reading.")
    if state == "permission-named":
        return ("grant the named permission. The feature is on, so this is the "
                "ordinary permissions path and not this note.")
    if state == "status-mismatch":
        return ("enable the feature anyway, then diagnose the recorded status "
                "separately. Two causes were in play and only one of them is "
                "addressed here.")
    if state == "feature-enabled":
        return ("look at the credential. Nothing about the repository's "
                "features explains this refusal.")
    return ("name the endpoint that refused you with --endpoint so the flag can "
            "be mapped to it.")


def get(session, path):
    """One GET. Returns the response object."""
    r = session.get(API + path, timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed "
                         "or revoked")
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo", help="owner/name of the repository")
    ap.add_argument("--endpoint", default="",
                    help="the endpoint that refused you, e.g. "
                         "/code-scanning/alerts. A full URL is accepted.")
    ap.add_argument("--status", default="",
                    help="the status code you recorded from it")
    ap.add_argument("--accepted-permissions", default="",
                    help="x-accepted-github-permissions off that response, if "
                         "it carried one")
    ap.add_argument("--probe", action="store_true",
                    help="also GET each mapped endpoint to record its status. "
                         "Reads only, and the same call your job already makes.")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2
    if "/" not in args.repo:
        log.error("repo should be owner/name")
        return 2

    probes = len(ENDPOINT_FEATURES) if args.probe else 0
    log.info("read cost: %d request(s) against the core hourly quota",
             read_cost(probes))

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

    log.info("%s: private=%s", args.repo, repo.get("private"))
    block = security_block(repo)
    if block is None:
        log.info("security_and_analysis: not returned. That block is only sent "
                 "to a caller with admin on the repository.")
    else:
        log.info("security_and_analysis: %s", " ".join(
            "%s=%s" % (f, flag_state(repo, f, "security"))
            for f in SECURITY_FEATURES))
    log.info("toggles: %s", " ".join(
        "%s=%s" % (t, repo.get(t)) for t in TOGGLES))

    rows = matrix(repo)
    probed = {}
    if args.probe:
        for row in rows:
            r = get(session, "/repos/%s%s" % (args.repo, row["endpoint"]))
            probed[row["endpoint"]] = r.status_code
            log.info("probe %s -> HTTP %s (flag %s)", row["endpoint"],
                     r.status_code, row["state"])

    target = feature_for(args.endpoint) if args.endpoint else None
    if target:
        target["state"] = flag_state(repo, target["feature"], target["source"])
        log.info("%s -> %s (%s), %s when disabled", target["endpoint"],
                 target["feature"], target["confidence"],
                 target["status_when_disabled"])
    state, detail = classify(repo, target, args.status,
                             args.accepted_permissions)
    log.info("%s: %s", state, detail)
    if target and plan_may_be_the_constraint(repo, target["feature"]):
        log.info("plan-note: this is a private or internal repository, so "
                 "availability depends on the plan as well as on the checkbox.")
    log.info("repair: %s", repair(state, target, repo))

    print(json.dumps({
        "repository": args.repo,
        "private": repo.get("private"),
        "visibility": repo.get("visibility"),
        "security_block_returned": block is not None,
        "security_and_analysis": {
            f: flag_state(repo, f, "security") for f in SECURITY_FEATURES},
        "toggles": {t: repo.get(t) for t in TOGGLES},
        "matrix": rows,
        "probed": probed,
        "endpoint": target,
        "state": state,
        "detail": detail,
        "repair": repair(state, target, repo),
    }, indent=2, default=str))
    return 1 if state in ("feature-disabled", "status-mismatch") else 0


if __name__ == "__main__":
    sys.exit(main())
