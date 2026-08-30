"""Report what a repository's commit signatures actually say.

Read only. One GET per page of commits and one for the branch rules. Nothing
is signed, no ruleset is created, and no commit is touched: where a rule is
missing the script prints the request for an admin to action.

The point of the note: verification.verified is one field of five, and reading
it alone throws away the difference between "nobody signed this", "somebody
signed it badly", "the signature is good and the key is not registered" and
"GitHub could not check". Those have four different repairs. A verification
object that is absent is a fifth state and it is not a false one.

What this can and cannot see: GitHub records the verification result, so this
reports what GitHub concluded at signing time, dated by verified_at. It does
not re-verify anything locally and it cannot tell you whether the signing key
is still trusted today.

Environment:

    GITHUB_TOKEN    a read-only token that can see the repository
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_commit_signatures")

API = "https://api.github.com"
UA = "github-commit-signatures/1.0"

# Every documented value of verification.reason, mapped to the family whose
# repair it shares. Written as a table rather than a chain of conditionals so
# that a reason GitHub adds later lands in "unknown-reason" and is reported,
# instead of silently taking the else branch of somebody's if.
REASONS = {
    "valid": ("verified",
              "the signature was checked and the committer identity resolved."),
    "unsigned": ("unsigned",
                 "the commit object carries no signature at all."),
    "invalid": ("signature-rejected",
                "a signature is present and did not verify against the key."),
    "malformed_signature": ("signature-rejected",
                            "the signature could not be parsed."),
    "expired_key": ("signature-rejected",
                    "the key that made the signature has expired."),
    "not_signing_key": ("signature-rejected",
                        "the key is not flagged for signing."),
    "unknown_signature_type": ("signature-rejected",
                               "the signature is not a type GitHub verifies."),
    "unknown_key": ("identity-not-linked",
                    "the key that made the signature is not registered to any "
                    "GitHub account. The cryptography is fine; the account "
                    "link is missing."),
    "no_user": ("identity-not-linked",
                "no GitHub account owns the committer email address."),
    "unverified_email": ("identity-not-linked",
                         "the committer email belongs to an account and has "
                         "not been verified on it."),
    "bad_email": ("identity-not-linked",
                  "the committer email is not among the identities on the key."),
    "gpgverify_error": ("github-could-not-check",
                        "GitHub's verification service errored. This is not a "
                        "statement about the commit."),
    "gpgverify_unavailable": ("github-could-not-check",
                              "GitHub's verification service was unavailable. "
                              "This is not a statement about the commit."),
}

# Order the tally is printed in, and the order the grade considers them.
FAMILIES = ("verified", "unsigned", "signature-rejected", "identity-not-linked",
            "github-could-not-check", "verification-absent", "unknown-reason")

# Families that are a real finding about this repository. Deliberately excludes
# github-could-not-check: an outage in GitHub's checker is somebody else's
# incident and paging on it teaches people to ignore the alert.
VIOLATIONS = ("unsigned", "signature-rejected")


def read_cost(pages, with_rules):
    """REST requests this run will spend. Pure. Printed before any are spent."""
    return max(1, int(pages)) + (1 if with_rules else 0)


def verification_of(commit):
    """Normalise one commit's verification object. Pure.

    Returns a dict with an explicit `present` flag. Every caller downstream
    branches on that flag first, so an endpoint that omits the object can never
    be read as a commit that failed verification, which is the mistake the
    whole note is about.
    """
    inner = (commit or {}).get("commit") or {}
    raw = inner.get("verification")
    if not isinstance(raw, dict):
        return {"present": False, "verified": None, "reason": None,
                "has_signature": False, "verified_at": None}
    signature = raw.get("signature")
    return {
        "present": True,
        "verified": raw.get("verified"),
        "reason": raw.get("reason"),
        "has_signature": bool(signature),
        "verified_at": raw.get("verified_at"),
    }


def family_of(verification):
    """Sort one normalised verification into its family. Pure. (family, detail).

    The boolean is checked against the reason rather than trusted on its own:
    the only reason that accompanies a true is `valid`, so a true beside any
    other reason is a shape this script does not recognise and says so.
    """
    if not verification.get("present"):
        return ("verification-absent",
                "this payload carried no verification object. That is unknown, "
                "not unsigned, and it must not be counted as either.")
    reason = verification.get("reason")
    verified = verification.get("verified")
    if reason is None:
        return ("unknown-reason",
                "the verification object has no reason field, so the boolean "
                "is the only evidence and it is not enough to act on.")
    known = REASONS.get(str(reason))
    if known is None:
        return ("unknown-reason",
                "reason %r is not one this script knows. Report it rather than "
                "letting it fall into a default." % reason)
    family, detail = known
    if family == "verified" and verified is not True:
        return ("unknown-reason",
                "reason is valid and verified is not true, which is a shape "
                "GitHub does not normally produce. Treat it as unknown.")
    if family != "verified" and verified is True:
        return ("unknown-reason",
                "verified is true beside reason %r. Only valid accompanies a "
                "true, so this pair is not readable." % reason)
    return (family, detail)


def identity_split(commit):
    """What the commit says about who wrote it. Pure. (state, detail).

    Reported alongside the signature and never instead of it. The author and
    committer strings are set by the client and are not authenticated by
    anything; the top-level author and committer objects are the GitHub
    accounts those emails resolve to, or null.
    """
    inner = (commit or {}).get("commit") or {}
    author_email = ((inner.get("author") or {}).get("email")) or ""
    committer_email = ((inner.get("committer") or {}).get("email")) or ""
    linked_author = (commit or {}).get("author")
    linked_committer = (commit or {}).get("committer")
    if not author_email and not committer_email:
        return ("no-emails",
                "the commit carries no author or committer email to compare.")
    if author_email.lower() != committer_email.lower():
        return ("author-differs-from-committer",
                "the author and the committer are different identities, and a "
                "signature speaks for the committer. A verified commit here "
                "does not assert the author consented to it.")
    if linked_author is None or linked_committer is None:
        return ("email-resolves-to-no-account",
                "an email on this commit resolves to no GitHub account, so "
                "there is no account for a signature to be matched against.")
    return ("author-is-committer",
            "author and committer are the same identity and both resolve to "
            "GitHub accounts.")


def author_allowlist_pass(commit, allowed):
    """The check people actually wrote. Pure. True, False or None.

    Kept in the script on purpose so the two policies can be run over the same
    commits and the disagreement counted. It reads commit.author.email, which
    is a string the committing client chose, and it authenticates nothing.
    """
    if not allowed:
        return None
    inner = (commit or {}).get("commit") or {}
    email = (((inner.get("author") or {}).get("email")) or "").lower()
    return email in {str(a).strip().lower() for a in allowed if str(a).strip()}


def signature_pass(commit):
    """The check the policy meant. Pure. True, False or None for unknown.

    None is a first-class answer here. A commit GitHub could not check, and a
    commit whose verification object never arrived, are both unknown, and
    collapsing either into a boolean is how a policy ends up fail-open or
    fail-noisy depending on which default somebody picked.
    """
    family, _ = family_of(verification_of(commit))
    if family == "verified":
        return True
    if family in VIOLATIONS or family == "identity-not-linked":
        return False
    return None


def disagreements(commits, allowed):
    """Where the two checks differ, commit by commit. Pure. List of dicts."""
    out = []
    for commit in commits or []:
        naive = author_allowlist_pass(commit, allowed)
        careful = signature_pass(commit)
        if naive is None or naive == careful:
            continue
        out.append({
            "sha": (commit or {}).get("sha"),
            "author_check": naive,
            "signature_check": careful,
            "gap": "author-passed-signature-did-not" if naive
                   else "signature-passed-author-did-not",
        })
    return out


def tally(commits):
    """Count the families across a list of commits. Pure. dict."""
    counts = {name: 0 for name in FAMILIES}
    for commit in commits or []:
        family, _ = family_of(verification_of(commit))
        counts[family] = counts.get(family, 0) + 1
    return counts


def enforcement_from_rules(rules, readable=True):
    """Is a signature rule actually in force on the branch. Pure. (state, detail).

    An unreadable answer is its own state. Saying "no rule" when the rules
    could not be read would tell somebody their branch is unprotected on the
    strength of a permission problem.
    """
    if not readable:
        return ("rule-unreadable",
                "the branch rules could not be read with this token, so "
                "whether signatures are enforced is unknown. That is not the "
                "same as unenforced.")
    if not isinstance(rules, list):
        return ("rule-unreadable",
                "the rules endpoint did not return a list, so nothing can be "
                "concluded about enforcement.")
    for rule in rules:
        if isinstance(rule, dict) and rule.get("type") == "required_signatures":
            return ("enforced",
                    "a required_signatures rule is active on this branch, so "
                    "an unsigned push is rejected rather than reported.")
    return ("no-rule",
            "no required_signatures rule is active on this branch. Whatever "
            "the history shows, the next push is free to be unsigned.")


def grade(counts, enforcement_state):
    """The finding, in one word. Pure. (state, detail)."""
    counts = counts or {}
    if counts.get("verification-absent"):
        return ("verification-unknown",
                "%d commit(s) arrived with no verification object. Until that "
                "is understood, no percentage from this run is trustworthy."
                % counts["verification-absent"])
    violations = sum(counts.get(name, 0) for name in VIOLATIONS)
    if violations:
        return ("unsigned-or-rejected-present",
                "%d commit(s) are unsigned or carry a signature that did not "
                "verify. This is the finding a signed-commit policy exists to "
                "produce." % violations)
    if counts.get("identity-not-linked"):
        return ("identity-not-linked-present",
                "%d commit(s) carry a good signature from a key no GitHub "
                "account claims. Nothing is cryptographically wrong; a public "
                "key needs uploading." % counts["identity-not-linked"])
    if counts.get("unknown-reason"):
        return ("unreadable-verification",
                "%d commit(s) have a verification shape this script does not "
                "recognise. Report them rather than grading them."
                % counts["unknown-reason"])
    if counts.get("github-could-not-check"):
        return ("checker-unavailable",
                "%d commit(s) could not be checked by GitHub. That is an "
                "outage, not a violation, and re-reading later is the whole "
                "response." % counts["github-could-not-check"])
    if enforcement_state == "enforced":
        return ("verified-and-enforced",
                "every commit read is verified and a rule requires it, which "
                "is the only combination that is a guarantee.")
    return ("verified-but-not-enforced",
            "every commit read is verified and nothing requires it. That is a "
            "description of past behaviour, not a constraint on the next push.")


def repair(state, enforcement_state, repo, branch):
    """The sentence a reader has to act on. Pure. Nothing here is executed."""
    lines = []
    if state == "unsigned-or-rejected-present":
        lines.append("find the commits listed as unsigned or signature-rejected "
                     "and get them re-signed or reverted")
    if state == "identity-not-linked-present":
        lines.append("ask the key owners to add their public keys to their "
                     "GitHub accounts; the signatures are already good")
    if state == "verification-unknown":
        lines.append("find out why a verification object was missing before "
                     "reporting any signing percentage from this repository")
    if state == "checker-unavailable":
        lines.append("re-read later: GitHub could not check these commits and "
                     "that is not a fact about your repository")
    if enforcement_state == "no-rule":
        lines.append("ask an admin of %s to add a ruleset requiring signed "
                     "commits on %s, so unsigned pushes are rejected rather "
                     "than reported" % (repo, branch or "the default branch"))
    if enforcement_state == "rule-unreadable":
        lines.append("re-run with a token that can read branch rules on %s, "
                     "because unreadable is not unenforced" % repo)
    if not lines:
        lines.append("nothing to repair from this reading")
    return ". ".join(lines) + ". Nothing here writes."


def get(session, path):
    """One GET. Returns the response object."""
    response = session.get(API + path, timeout=30)
    if response.status_code == 401:
        log.warning("401 on %s: the credential was not accepted, which is a "
                    "different note", path)
    return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", help="owner/name")
    parser.add_argument("--ref", help="branch, tag or sha to walk from")
    parser.add_argument("--pages", type=int, default=1,
                        help="pages of 100 commits to read, default 1")
    parser.add_argument("--branch",
                        help="read the rules in force on this branch")
    parser.add_argument("--author-allowlist", default="",
                        help="comma-separated author emails, to run the naive "
                             "check beside the real one")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    log.info("read cost: %d REST request(s) against the core hourly quota",
             read_cost(args.pages, bool(args.branch)))

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub refuses requests with no User-Agent before it looks at auth.
        "User-Agent": UA,
    })

    commits = []
    for page in range(1, max(1, args.pages) + 1):
        path = "/repos/%s/commits?per_page=100&page=%d" % (args.repo, page)
        if args.ref:
            path += "&sha=" + args.ref
        response = get(session, path)
        if response.status_code != 200:
            log.error("GET %s -> HTTP %s; stopping", path, response.status_code)
            break
        batch = response.json()
        if not isinstance(batch, list) or not batch:
            break
        commits.extend(batch)
    log.info("%d commit(s) read from %s", len(commits), args.repo)

    counts = tally(commits)
    log.info("verified: %d  unsigned: %d  signature-rejected: %d  "
             "identity-not-linked: %d  github-could-not-check: %d  "
             "verification-absent: %d",
             counts["verified"], counts["unsigned"], counts["signature-rejected"],
             counts["identity-not-linked"], counts["github-could-not-check"],
             counts["verification-absent"])

    allowed = [part for part in args.author_allowlist.split(",") if part.strip()]
    gaps = disagreements(commits, allowed)
    if allowed:
        missed = [g for g in gaps if g["gap"] == "author-passed-signature-did-not"]
        log.info("author-check-disagreement: %d commit(s) the author allowlist "
                 "passed and the signature check did not", len(missed))

    splits = {}
    for commit in commits:
        state, _ = identity_split(commit)
        splits[state] = splits.get(state, 0) + 1
    log.info("identity: %s", splits)

    rules, readable = None, False
    if args.branch:
        path = "/repos/%s/rules/branches/%s" % (args.repo, args.branch)
        response = get(session, path)
        readable = response.status_code == 200
        rules = response.json() if readable else None
    enforcement_state, enforcement_detail = enforcement_from_rules(
        rules, readable if args.branch else False)
    if args.branch:
        log.info("enforcement: %s. %s", enforcement_state, enforcement_detail)

    state, detail = grade(counts, enforcement_state)
    log.info("%s: %s", state, detail)
    fix = repair(state, enforcement_state if args.branch else "not-read",
                 args.repo, args.branch)
    log.info("repair: %s", fix)

    print(json.dumps({
        "repository": args.repo,
        "commits_read": len(commits),
        "counts": counts,
        "identity_split": splits,
        "disagreements": gaps[:20],
        "disagreement_count": len(gaps),
        "enforcement_state": enforcement_state if args.branch else "not-read",
        "state": state,
        "detail": detail,
        "repair": fix,
    }, indent=2, default=str))
    return 1 if state in ("unsigned-or-rejected-present", "verification-unknown",
                          "identity-not-linked-present",
                          "verified-but-not-enforced") else 0


if __name__ == "__main__":
    sys.exit(main())
