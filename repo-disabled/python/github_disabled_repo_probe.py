"""Recognise a disabled repository and keep its zeroes out of your aggregates.

Read only. One GET for the repository object and one cheap GET per probed
sub-resource, all at per_page=1. Nothing is written, and no write is attempted
to characterise the state: disabled is a boolean on the repository object and
the sub-resource probes are reads that would have happened anyway in the sweep
this note is about.

The point of the note: a disabled repository -- switched off by GitHub for a
billing problem, a terms violation or a suspended owning account -- keeps
appearing in organisation listings and keeps serving its own repository object
while most of its sub-resources stop answering. It is therefore present enough
to be counted and absent enough to contribute nothing, so every org-wide
aggregate silently records it as zero of everything.

What this can and cannot see: the boolean is exact and the pattern of answers
is real evidence. Why the repository was disabled is not exposed anywhere in
the API; billing, a terms violation and a suspended account are
indistinguishable from here, so the script names the owner of the remedy
rather than the reason.

Environment:

    GITHUB_TOKEN    a token with read access to the repositories
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_disabled_repo_probe")

API = "https://api.github.com"
UA = "github-disabled-repo-probe/1.0"

# Cheap reads that a repository normally answers. per_page=1 everywhere: the
# question is whether the endpoint answers at all, not what is in it.
DEFAULT_PROBES = ("/branches?per_page=1", "/commits?per_page=1",
                  "/contributors?per_page=1", "/languages")

# An empty repository answers this on anything that needs a commit. It is the
# one false positive this check can produce, so it gets its own state.
EMPTY_REPOSITORY = 409


def platform_state(repo):
    """Which platform state this repository is in. Pure.

    disabled and archived are separate booleans on the same object with
    different owners and different remedies, so they make four states rather
    than one flag with two names.
    """
    if not isinstance(repo, dict):
        return "unknown"
    disabled = bool(repo.get("disabled"))
    archived = bool(repo.get("archived"))
    if disabled and archived:
        return "disabled-and-archived"
    if disabled:
        return "disabled"
    if archived:
        return "archived"
    return "active"


def is_disabled(state):
    """Whether the disabled boolean is set, in either combination. Pure."""
    return state in ("disabled", "disabled-and-archived")


def explains_subresource(state, status):
    """Whether the repository state accounts for this answer. Pure.

    Returns (explained, why). A failure on a repository that is not disabled
    is deliberately not explained away: that is a credential triage and it
    belongs to another note.
    """
    try:
        code = int(status)
    except (TypeError, ValueError):
        return (False, "no readable status for this probe.")
    if 200 <= code < 300:
        return (True, "answered")
    if code == EMPTY_REPOSITORY:
        return (False, "409, which is an empty repository rather than a "
                       "disabled one")
    if is_disabled(state) and code in (403, 404, 451):
        return (True, "explained by the disabled state")
    if state == "archived" and code in (403, 404):
        return (False, "not explained by archiving, which leaves reads working")
    return (False, "not explained by the repository state")


def probe_verdict(state, probes):
    """Classify a repository from its state and its probe answers. Pure.

    probes: [{"path": str, "status": int}, ...]
    """
    rows = [p for p in (probes or []) if isinstance(p, dict)]
    failing = [p for p in rows
               if not explains_subresource(state, p.get("status"))[0]
               or not (200 <= int(p.get("status") or 0) < 300)]
    empty = [p for p in rows if str(p.get("status")) == str(EMPTY_REPOSITORY)]

    if state == "unknown":
        return ("repository-unreadable",
                "the repository object itself did not come back, so this is a "
                "credential or name problem rather than a platform state.")
    if is_disabled(state):
        if failing:
            return ("ghost-confirmed",
                    "the repository object reads and %d of %d sub-resource(s) "
                    "do not, which is what disabled looks like from the outside."
                    % (len(failing), len(rows)))
        return ("disabled-but-answering",
                "disabled is set and every probe answered anyway. Trust the "
                "boolean: the repository is switched off and must still be "
                "excluded from aggregates.")
    if empty:
        return ("empty-repository",
                "%d probe(s) answered 409 Git Repository is empty. This "
                "repository has never been pushed to and is not disabled."
                % len(empty))
    if state == "archived":
        return ("archived-not-disabled",
                "archived rather than disabled. Reads work and only writes are "
                "refused, which is a different note.")
    if failing:
        return ("not-explained-by-state",
                "%d sub-resource(s) failed on a repository that is neither "
                "disabled nor archived, so the repository state does not "
                "explain it." % len(failing))
    return ("healthy", "the repository reads and every sub-resource answered.")


def aggregate_safety(state):
    """Whether this repository may enter an org-wide aggregate. Pure."""
    if is_disabled(state):
        return ("exclude",
                "every zero this repository contributes is an artefact of the "
                "disabled state rather than a measurement.")
    if state == "unknown":
        return ("exclude",
                "the repository could not be read, so it has no values to "
                "contribute and its absence should be visible in the report.")
    if state == "archived":
        return ("include",
                "an archived repository is fully readable, so its values are "
                "real. Only its writes are refused.")
    return ("include", "nothing here disqualifies this repository from a count.")


def is_real_zero(state, value):
    """Whether a zero measured on this repository means anything. Pure.

    Returns True for a genuine zero, False for an artefact, None where the
    value is not a zero at all.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number != 0:
        return None
    if is_disabled(state) or state == "unknown":
        return False
    return True


def aggregate_impact(rows):
    """What a sweep should report alongside its total. Pure."""
    counted, excluded, false_zeroes = 0, 0, 0
    for row in rows or []:
        state = (row or {}).get("state")
        decision, _ = aggregate_safety(state)
        if decision == "exclude":
            excluded += 1
            if is_disabled(state):
                false_zeroes += 1
        else:
            counted += 1
    return {"counted": counted, "excluded": excluded,
            "false_zeroes_avoided": false_zeroes}


def remedy_owner(state):
    """Who can actually change this state. Pure."""
    if is_disabled(state):
        return ("GitHub, through the billing or support relationship for this "
                "account. The API does not say which reason applies.")
    if state == "archived":
        return ("whoever owns the repository, by unarchiving it. That is a "
                "decision about whether it is still in use.")
    if state == "unknown":
        return "nobody yet: the repository could not be read."
    return "no remedy needed."


def repair(state):
    """The sentence a reader has to act on. Pure."""
    if state in ("ghost-confirmed", "disabled-but-answering"):
        return ("exclude this repository from org-wide aggregates and report it "
                "separately. Nothing in your integration can re-enable it; that "
                "is a billing or account matter with GitHub.")
    if state == "empty-repository":
        return ("nothing. A repository that has never been pushed to answers "
                "409 on anything needing a commit, and that is not this "
                "problem.")
    if state == "archived-not-disabled":
        return ("see /github/repo-archived-writes-403/ -- reads work there and "
                "only writes are refused.")
    if state == "not-explained-by-state":
        return ("triage the failures as a credential problem: the repository "
                "state does not account for them.")
    if state == "repository-unreadable":
        return ("check the name, the visibility and the installation before "
                "anything else. A 404 on the repository means several things.")
    return "nothing on the platform state."


def read_cost(repos, probes=DEFAULT_PROBES):
    """Requests this run will spend against the core quota. Pure."""
    per_repo = 1 + len(probes or ())
    return per_repo * len(repos or [])


def get_repo(session, full_name):
    """One GET of a repository object. Returns (status, dict-or-None)."""
    r = session.get(API + "/repos/" + full_name, timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed "
                         "or revoked")
    if r.status_code == 403 and "rate limit" in r.text.lower():
        raise SystemExit("403 rate limited. GET /rate_limit reports the reset "
                         "time and does not itself consume quota")
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, (body if isinstance(body, dict) else None)


def probe(session, full_name, path):
    """One cheap GET of a sub-resource. Returns its status only."""
    r = session.get(API + "/repos/" + full_name + path, timeout=30)
    return r.status_code


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", action="append", required=True,
                    help="owner/name to check. Repeatable.")
    ap.add_argument("--probe", action="append", default=[],
                    help="sub-resource path to probe, defaulting to branches, "
                         "commits, contributors and languages")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    probes = tuple(args.probe) or DEFAULT_PROBES
    log.info("read cost: %d request(s) per repository against the core hourly "
             "quota", 1 + len(probes))
    log.info("read cost: %d request(s) in total", read_cost(args.repo, probes))

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })

    findings = []
    for name in args.repo:
        status, repo = get_repo(session, name)
        state = platform_state(repo) if status == 200 else "unknown"
        results = []
        if status == 200:
            for path in probes:
                code = probe(session, name, path)
                explained, why = explains_subresource(state, code)
                results.append({"path": path.split("?")[0], "status": code,
                                "explained": explained, "why": why})

        verdict, detail = probe_verdict(state, results)
        decision, reason = aggregate_safety(state)

        log.info("%s: disabled=%s archived=%s", name,
                 bool((repo or {}).get("disabled")),
                 bool((repo or {}).get("archived")))
        log.info("%s: %s", verdict, detail)
        for row in results:
            log.info("  %s %s %s", row["path"], row["status"], row["why"])
        log.info("  aggregates: %s. %s", decision, reason)
        log.info("  remedy owner: %s", remedy_owner(state))
        log.info("  repair: %s", repair(verdict))

        findings.append({
            "repository": name,
            "repository_status": status,
            "platform_state": state,
            "probes": results,
            "state": verdict,
            "detail": detail,
            "aggregate_decision": decision,
            "aggregate_reason": reason,
            "remedy_owner": remedy_owner(state),
            "repair": repair(verdict),
        })

    impact = aggregate_impact([{"state": f["platform_state"]} for f in findings])
    disabled = sum(1 for f in findings if is_disabled(f["platform_state"]))
    archived = sum(1 for f in findings
                   if f["platform_state"] in ("archived", "disabled-and-archived"))
    log.info("summary: %d repositories, %d disabled, %d archived, %d countable",
             len(findings), disabled, archived, impact["counted"])

    print(json.dumps({
        "requests_spent": read_cost(args.repo, probes),
        "aggregate_impact": impact,
        "findings": findings,
    }, indent=2, default=str))
    return 1 if disabled else 0


if __name__ == "__main__":
    sys.exit(main())
