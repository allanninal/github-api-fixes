"""Prove whether the credential is the variable, by running two of them.

Read only. One GET per rung per credential, at most eight requests, none of
which needs a scope and none of which writes.

An expired token, a revoked token and a truncated token all return the same
401 Bad credentials, so this does not try to tell them apart. It answers the
question that is actually answerable: is the credential the thing that changed,
or did the world change underneath it.
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_credential_differential")

API = "https://api.github.com"
UA = "github-credential-differential/1.0"


def ladder(repo=None, org=None):
    """The rungs this run can actually probe, in order of what they need. Pure.

    The public rung is first on purpose: it is the one that needs no credential
    at all, so a 401 there is the strongest single observation in the check.
    """
    rungs = [("public", "/"), ("identity", "/user")]
    if repo:
        rungs.append(("repository", "/repos/" + str(repo)))
    if org:
        rungs.append(("organization", "/orgs/" + str(org)))
    return rungs


def outcome(status):
    """Reduce a status code to what it says about a credential. Pure."""
    try:
        status = int(status)
    except (TypeError, ValueError):
        return "error"
    if status == 0:
        return "error"
    if 200 <= status < 300:
        return "ok"
    if status == 401:
        return "unauthenticated"
    if status == 403:
        return "forbidden"
    if status == 404:
        return "missing"
    return "other"


def shape(rows):
    """Name the signature of a failure across the ladder. Pure.

    rows: [(rung, outcome), ...]

    The distinction that carries the note is uniform against selective. Expiry
    is total, because presenting a rejected credential is worse than presenting
    none, so a credential that answers 200 to anything has not expired.
    """
    results = [result for _rung, result in rows or []]
    if not results:
        return "nothing-probed"
    if all(result == "ok" for result in results):
        return "healthy"
    if all(result == "unauthenticated" for result in results):
        return "uniform-401"
    if any(result == "ok" for result in results):
        return "selective"
    return "mixed"


def compare(suspect, control):
    """Line the two ladders up rung by rung. Pure.

    A rung the control never ran comes back with control None and agrees False,
    so a partial control cannot be mistaken for agreement.
    """
    lookup = dict(control or [])
    rows = []
    for rung, result in suspect or []:
        other = lookup.get(rung)
        rows.append({"rung": rung, "suspect": result, "control": other,
                     "agrees": other is not None and other == result})
    return rows


def diagnose(suspect, control=None):
    """Read the two ladders side by side. Pure.

    Returns (state, detail). The state never says "expired", because nothing
    observable distinguishes expiry from revocation or truncation. It says what
    the evidence supports, which is whether the credential is the variable.
    """
    suspect_shape = shape(suspect)

    if suspect_shape == "nothing-probed":
        return ("nothing-probed",
                "no rungs were run, so there is nothing to compare.")

    if not control:
        if suspect_shape == "healthy":
            return ("suspect-healthy",
                    "the suspect credential answered 200 on every rung, so "
                    "whatever is failing is not this credential.")
        if suspect_shape == "uniform-401":
            return ("no-control",
                    "every rung answered 401, including the one that needs no "
                    "credential at all. That is the signature of a value the "
                    "server will not accept, and expiry, revocation and a "
                    "truncated string all produce it identically. Without a "
                    "second credential run at the same instant, the evidence "
                    "stops here.")
        return ("no-control",
                "the suspect failed as %s rather than uniformly, which is not "
                "what an expired credential looks like: expiry is total. "
                "Without a control credential this cannot be taken further."
                % suspect_shape)

    rows = compare(suspect, control)
    control_shape = shape(control)

    if suspect_shape == "healthy":
        return ("suspect-healthy",
                "the suspect credential answered 200 on every rung, so whatever "
                "is failing is not this credential.")

    if suspect_shape == "uniform-401" and control_shape == "uniform-401":
        return ("both-dead",
                "both credentials answered 401 on every rung. Two tokens do not "
                "expire in the same second, so look at what they share: the "
                "store they came from, the network they left by, and the "
                "organization that can revoke them together.")

    if suspect_shape == "uniform-401" and control_shape == "healthy":
        return ("credential-is-the-variable",
                "the suspect answered 401 on every rung including the public "
                "one, at the same instant the control answered 200 on all of "
                "them. The repository, the organization, the network and your "
                "code are eliminated: the credential is the only thing that "
                "differs. Expiry is the common reason, and revocation and "
                "truncation look identical from here.")

    if all(row["agrees"] for row in rows):
        failing = [row["rung"] for row in rows if row["suspect"] != "ok"]
        return ("resource-changed",
                "both credentials answer identically on every rung, and %s "
                "failed for both. The thing that changed is the resource, not "
                "the token: a repository renamed, transferred or deleted "
                "answers the same way to everybody." % ", ".join(failing))

    if suspect_shape == "selective":
        differing = ["%s (%s)" % (row["rung"], row["suspect"])
                     for row in rows if not row["agrees"]]
        return ("access-not-expiry",
                "the suspect answered 200 on at least one rung, so it has not "
                "expired: an expired credential cannot authenticate anything. "
                "It differs from the control at %s. Look at what that "
                "credential is allowed to reach rather than at its calendar."
                % ", ".join(differing))

    return ("mixed",
            "the two credentials fail in different ways (%s against %s), which "
            "is neither an expiry nor a changed resource. Report the rungs "
            "rather than picking a story." % (suspect_shape, control_shape))


def run_ladder(token, rungs):
    """One GET per rung. Returns [(rung, outcome), ...]."""
    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })
    rows = []
    for rung, path in rungs:
        try:
            status = session.get(API + path, timeout=30).status_code
        except requests.RequestException as exc:
            log.error("GET %s failed: %s", path, exc)
            status = 0
        rows.append((rung, outcome(status)))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="owner/name, adds a repository rung")
    parser.add_argument("--org", help="login, adds an organization rung")
    parser.add_argument("--env", default="GITHUB_TOKEN",
                        help="environment variable holding the suspect")
    parser.add_argument("--control-env", default="GITHUB_CONTROL_TOKEN",
                        help="environment variable holding a known-good control")
    args = parser.parse_args()

    suspect_token = os.environ.get(args.env)
    if not suspect_token:
        log.error("set %s to the credential under suspicion", args.env)
        return 2

    rungs = ladder(args.repo, args.org)
    suspect = run_ladder(suspect_token, rungs)

    control_token = os.environ.get(args.control_env)
    control = run_ladder(control_token, rungs) if control_token else None
    if not control_token:
        log.warning("%s is not set. Without a control credential this is a "
                    "description of a 401 rather than a diagnosis.",
                    args.control_env)

    log.info("%-14s %-8s %s", "rung", "suspect", "control")
    for row in compare(suspect, control or []):
        log.info("%-14s %-8s %s", row["rung"], row["suspect"],
                 row["control"] or "-")

    state, detail = diagnose(suspect, control)
    log.info("%s: %s", state, detail)

    if state == "credential-is-the-variable":
        log.info("repair: re-mint the credential, then record its expiry date "
                 "in the same place the secret is stored and alert before it.")
        log.info("repair: for unattended automation, authenticate as a GitHub "
                 "App installation. Its one-hour tokens are minted "
                 "automatically and never need a calendar entry.")
    if state == "both-dead":
        log.info("repair: look at the secrets store, the egress path and any "
                 "organization policy that could revoke both at once.")

    print(json.dumps({"state": state, "suspect": suspect,
                      "control": control}, indent=2))
    return 1 if state not in ("suspect-healthy",) else 0


if __name__ == "__main__":
    sys.exit(main())
