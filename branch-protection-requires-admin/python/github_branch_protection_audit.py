"""Audit branch protection without mistaking a refusal for an absence.

Read only. Three GETs per branch and nothing is written: no commit is created,
no ref is updated, no protection setting is changed. What a push would be
refused for is derived from the rules the API publishes, never by attempting a
push and reading the error.

The point of the note: the detailed protection rules are readable only with
repository admin. Without it, GET .../protection answers 403 with an
admin-rights message, and an auditor that treats every non-200 as "not
protected" reports a fully protected estate as wide open. Only a 404 whose
message is "Branch not protected" is evidence of absence.

What this can and cannot see: with a read-only token the classic rules on a
protected branch are genuinely invisible, and that is reported as unknown
rather than guessed at. Two things are visible without admin and are used
instead -- the protected boolean on the branch object, and the rules a ruleset
contributes, which GET /repos/{owner}/{repo}/rules/branches/{branch} publishes
to anyone who can read the repository.

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
log = logging.getLogger("github_branch_protection_audit")

API = "https://api.github.com"
UA = "github-branch-protection-audit/1.0"

# The one message that turns a 404 into a finding. Anything else answering 404
# is ambiguous and belongs to the 404 triage note instead.
ABSENCE_MESSAGE = "branch not protected"

# What the protection endpoint says when the token can reach the repository but
# not its settings. Matched loosely because the wording has varied.
ADMIN_MESSAGE = "admin rights"

# Three reads per branch: the branch, the classic protection, the ruleset rules.
REQUESTS_PER_BRANCH = 3


def is_absence(status, message):
    """Whether this answer is evidence that the branch is unprotected. Pure.

    The single most important function in this script, and the one the broken
    auditors are missing. A 403 is never an absence: it says the token cannot
    see, which is a statement about the instrument rather than about the
    branch. A 404 is an absence only when it names the reason.
    """
    try:
        code = int(status)
    except (TypeError, ValueError):
        return False
    if code != 404:
        return False
    return ABSENCE_MESSAGE in str(message or "").lower()


def visibility(status, message):
    """What the protection endpoint's answer tells you. Pure.

    Returns one of: readable, not-protected, admin-required, ambiguous-404,
    unknown. Four of those five are not measurements of the branch.
    """
    try:
        code = int(status)
    except (TypeError, ValueError):
        return "unknown"
    if code == 200:
        return "readable"
    if is_absence(code, message):
        return "not-protected"
    if code == 403:
        return "admin-required"
    if code == 404:
        return "ambiguous-404"
    return "unknown"


def verdict(protected_flag, status, message, rules=None):
    """Classify one branch from all three readings. Pure. (state, detail).

    The branch object's boolean is authoritative for coverage because it is
    visible without admin. The protection endpoint decides only whether the
    detail is available, and the ruleset listing can rescue a row that would
    otherwise have been opaque.
    """
    seen = visibility(status, message)
    rule_count = len(rules or [])

    if protected_flag is None:
        return ("branch-unreadable",
                "the branch itself did not come back, so there is nothing to "
                "judge. That is a repository or credential problem rather than "
                "a protection one.")

    if protected_flag:
        if seen == "readable":
            return ("protected-rules-readable",
                    "the branch is protected and this token can read the rules, "
                    "so the refusals below are quoted from settings rather than "
                    "inferred.")
        if seen == "admin-required":
            detail = ("the branch reports protected=true and the protection "
                      "endpoint refused with admin rights required, so the "
                      "classic rules are not readable by this token.")
            if rule_count:
                detail += (" %d ruleset rule(s) are readable and are reported."
                           % rule_count)
            return ("protected-rules-hidden", detail)
        if seen == "not-protected":
            return ("contradictory",
                    "the branch says protected=true and the protection endpoint "
                    "says the branch is not protected. A ruleset governs this "
                    "branch without classic branch protection behind it.")
        return ("protected-rules-hidden",
                "the branch reports protected=true and the protection endpoint "
                "answered %s, which is not a readable rule set. Treat this as "
                "protected and unmeasured." % status)

    if rule_count:
        return ("ruleset-only",
                "protected=false, but %d rule(s) reach this branch from a "
                "ruleset. Classic protection is not the only thing that refuses "
                "a push." % rule_count)
    if seen == "not-protected":
        return ("unprotected-confirmed",
                "protected=false and the protection endpoint answered 404 "
                "Branch not protected, which is the one 404 that means absence.")
    if seen == "admin-required":
        return ("unprotected-by-flag",
                "protected=false on the branch object, which is visible without "
                "admin and is the honest reading. The protection endpoint "
                "refused separately and adds nothing here.")
    return ("unknown",
            "protected=false but the protection endpoint answered %s rather "
            "than a recognised absence, so this row is not resolved." % status)


def refused_writes(protection):
    """Plain statements of what the classic rules refuse. Pure.

    Derived from fields. Nothing here is learned by pushing anything.
    """
    if not isinstance(protection, dict):
        return []
    out = []
    reviews = protection.get("required_pull_request_reviews")
    if isinstance(reviews, dict):
        count = reviews.get("required_approving_review_count")
        if count:
            out.append("a direct push is refused: %s approving review(s) are "
                       "required through a pull request" % count)
        else:
            out.append("a direct push is refused: a pull request is required")
    checks = protection.get("required_status_checks")
    if isinstance(checks, dict):
        contexts = checks.get("contexts") or []
        out.append("a merge is refused until %d status check(s) pass"
                   % len(contexts))
        if checks.get("strict"):
            out.append("a merge is refused while the branch is behind its base")
    if (protection.get("enforce_admins") or {}).get("enabled"):
        out.append("administrators are not exempt from any of the above")
    restrictions = protection.get("restrictions")
    if isinstance(restrictions, dict):
        actors = (len(restrictions.get("users") or [])
                  + len(restrictions.get("teams") or [])
                  + len(restrictions.get("apps") or []))
        out.append("a push is refused for everyone except %d listed actor(s)"
                   % actors)
    if (protection.get("required_signatures") or {}).get("enabled"):
        out.append("an unsigned commit is refused")
    if (protection.get("lock_branch") or {}).get("enabled"):
        out.append("the branch is locked, so every write is refused")
    force = protection.get("allow_force_pushes")
    if isinstance(force, dict) and not force.get("enabled"):
        out.append("a force push is refused")
    deletions = protection.get("allow_deletions")
    if isinstance(deletions, dict) and not deletions.get("enabled"):
        out.append("deleting the branch is refused")
    return out


def refused_by_rules(rules):
    """The same statements, from the ruleset listing. Pure.

    This is the half that needs no admin, so on a read-only run it is often
    the only description of the branch anybody gets.
    """
    if not isinstance(rules, list):
        return []
    kinds = [r.get("type") for r in rules if isinstance(r, dict)]
    out = []
    if "pull_request" in kinds:
        out.append("a pull request is required, so a direct push to this "
                   "branch is refused")
    if "required_status_checks" in kinds:
        out.append("a merge is refused until the ruleset's status checks pass")
    if "non_fast_forward" in kinds:
        out.append("non-fast-forward updates are blocked, so a force push is "
                   "refused")
    if "deletion" in kinds:
        out.append("deleting the branch is refused")
    if "creation" in kinds:
        out.append("creating this ref is refused")
    if "update" in kinds:
        out.append("updating this ref directly is refused")
    if "required_signatures" in kinds:
        out.append("an unsigned commit is refused")
    return out


def rulesets_named(rules):
    """Which rulesets contributed the rules, for the report. Pure."""
    names = []
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        source = rule.get("ruleset_source") or rule.get("ruleset_source_type")
        if source and source not in names:
            names.append(source)
    return names


def push_allowlist(protection):
    """Who is allowed to push to a restricted branch. Pure. Names only."""
    restrictions = (protection or {}).get("restrictions")
    if not isinstance(restrictions, dict):
        return []
    out = []
    for user in restrictions.get("users") or []:
        if isinstance(user, dict) and user.get("login"):
            out.append("user:" + str(user["login"]))
    for team in restrictions.get("teams") or []:
        if isinstance(team, dict) and team.get("slug"):
            out.append("team:" + str(team["slug"]))
    for app in restrictions.get("apps") or []:
        if isinstance(app, dict) and app.get("slug"):
            out.append("app:" + str(app["slug"]))
    return out


def coverage(states):
    """Summarise a sweep without letting unknown become unprotected. Pure."""
    counts = {"protected": 0, "readable_in_detail": 0, "unprotected": 0,
              "unknown": 0}
    for state in states or []:
        if state in ("protected-rules-readable", "protected-rules-hidden",
                     "contradictory", "ruleset-only"):
            counts["protected"] += 1
            if state == "protected-rules-readable":
                counts["readable_in_detail"] += 1
        elif state in ("unprotected-confirmed", "unprotected-by-flag"):
            counts["unprotected"] += 1
        else:
            counts["unknown"] += 1
    return counts


def instrument_verdict(counts):
    """Whether the sweep measured the estate or measured its own token. Pure."""
    counts = counts or {}
    protected = int(counts.get("protected") or 0)
    detail = int(counts.get("readable_in_detail") or 0)
    unknown = int(counts.get("unknown") or 0)
    total = protected + int(counts.get("unprotected") or 0) + unknown
    if not total:
        return ("no-rows", "nothing was checked.")
    if unknown:
        return ("instrument-gap",
                "%d of %d row(s) are unresolved. Those are not findings about "
                "the estate." % (unknown, total))
    if protected and not detail:
        return ("coverage-only",
                "every protected branch was counted from its boolean and none "
                "of the classic rules were readable. Coverage is trustworthy, "
                "detail is absent.")
    return ("measured", "every row resolved to a state about the branch.")


def repair(state):
    """The sentence a reader has to act on. Pure."""
    if state == "protected-rules-hidden":
        return ("report this as protected. To read the detailed rules, grant "
                "this token repository admin or use an App with "
                "administration: read.")
    if state == "protected-rules-readable":
        return ("nothing on visibility. Check the rules against your policy; "
                "the refusals above are what a push actually meets.")
    if state == "unprotected-confirmed":
        return ("this branch really is unprotected. Protect it or record the "
                "exception.")
    if state == "unprotected-by-flag":
        return ("this branch is unprotected on the boolean that needs no "
                "admin. Do not upgrade the token to confirm an absence you can "
                "already see.")
    if state == "ruleset-only":
        return ("read the ruleset rather than the branch protection settings. "
                "A ruleset refuses pushes without setting protected=true.")
    if state == "contradictory":
        return ("audit the ruleset that governs this branch. Classic protection "
                "is not what is refusing writes here.")
    if state == "branch-unreadable":
        return ("triage the repository and the token before the protection: "
                "check the name, the visibility and the installation.")
    return ("record this row as unknown. An unresolved answer is not a finding "
            "and must never be counted as unprotected.")


def read_cost(branches):
    """Requests this run will spend against the core quota. Pure."""
    return REQUESTS_PER_BRANCH * len(branches or [])


def split_target(target):
    """owner/repo:branch into its three parts. Pure."""
    text = str(target or "").strip()
    if ":" in text:
        repo, branch = text.rsplit(":", 1)
    else:
        repo, branch = text, "main"
    if repo.count("/") != 1 or not branch:
        return None
    owner, name = repo.split("/")
    if not owner or not name:
        return None
    return (owner, name, branch)


def message_of(body):
    """The message field of an error body, if there is one. Pure."""
    if isinstance(body, dict):
        return str(body.get("message") or "")
    return ""


def get_json(session, path):
    """One GET. Returns (status, parsed-body-or-None). Never writes."""
    r = session.get(API + path, timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed "
                         "or revoked")
    if r.status_code == 403 and "rate limit" in r.text.lower():
        raise SystemExit("403 rate limited. GET /rate_limit reports the reset "
                         "time and does not itself consume quota")
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


def inspect(session, owner, name, branch):
    """All three readings for one branch. Reads only."""
    base = "/repos/%s/%s" % (owner, name)
    b_status, b_body = get_json(session, "%s/branches/%s" % (base, branch))
    flag = None
    if b_status == 200 and isinstance(b_body, dict):
        flag = bool(b_body.get("protected"))

    p_status, p_body = get_json(session, "%s/branches/%s/protection"
                                % (base, branch))
    protection = p_body if (p_status == 200 and isinstance(p_body, dict)) else None

    r_status, r_body = get_json(session, "%s/rules/branches/%s" % (base, branch))
    rules = r_body if (r_status == 200 and isinstance(r_body, list)) else []

    return {
        "branch_status": b_status,
        "protected_flag": flag,
        "protection_status": p_status,
        "protection_message": message_of(p_body),
        "protection": protection,
        "rules_status": r_status,
        "rules": rules,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--branch", action="append", required=True,
                    help="owner/repo:branch to audit. Repeatable. The branch "
                         "defaults to main when the colon is left off.")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    targets = []
    for raw in args.branch:
        parts = split_target(raw)
        if not parts:
            log.error("cannot parse %r: expected owner/repo:branch", raw)
            return 2
        targets.append(parts)

    log.info("read cost: at most %d request(s) per branch against the core "
             "hourly quota", REQUESTS_PER_BRANCH)
    log.info("read cost: at most %d request(s) in total", read_cost(targets))

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })

    findings = []
    for owner, name, branch in targets:
        label = "%s/%s:%s" % (owner, name, branch)
        seen = inspect(session, owner, name, branch)
        state, detail = verdict(seen["protected_flag"], seen["protection_status"],
                                seen["protection_message"], seen["rules"])
        refusals = refused_writes(seen["protection"]) or refused_by_rules(seen["rules"])

        log.info("%s protected=%s protection=%s rules=%d", label,
                 seen["protected_flag"], seen["protection_status"],
                 len(seen["rules"]))
        log.info("%s: %s", state, detail)
        for line in refusals:
            log.info("  %s", line)
        allowlist = push_allowlist(seen["protection"])
        if allowlist:
            log.info("  push allowed for: %s", ", ".join(allowlist))
        sources = rulesets_named(seen["rules"])
        if sources:
            log.info("  ruleset source(s): %s", ", ".join(str(s) for s in sources))
        log.info("repair: %s", repair(state))

        findings.append({
            "branch": label,
            "protected": seen["protected_flag"],
            "protection_status": seen["protection_status"],
            "protection_visibility": visibility(seen["protection_status"],
                                                seen["protection_message"]),
            "ruleset_rule_count": len(seen["rules"]),
            "ruleset_sources": sources,
            "refused_writes": refusals,
            "push_allowlist": allowlist,
            "state": state,
            "detail": detail,
            "repair": repair(state),
        })

    counts = coverage([f["state"] for f in findings])
    instrument, note = instrument_verdict(counts)
    log.info("summary: %d protected, %d readable in detail, %d unprotected, "
             "%d unknown", counts["protected"], counts["readable_in_detail"],
             counts["unprotected"], counts["unknown"])
    log.info("%s: %s", instrument, note)

    print(json.dumps({
        "requests_spent_at_most": read_cost(targets),
        "coverage": counts,
        "instrument": {"state": instrument, "detail": note},
        "findings": findings,
    }, indent=2, default=str))
    bad = {"unprotected-confirmed", "unprotected-by-flag"}
    return 1 if counts["unknown"] or any(f["state"] in bad for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
