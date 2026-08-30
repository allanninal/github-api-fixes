"""Say whether an account was removed from an organization by a 2FA rule.

Read only. Four GETs and nothing else. Re-inviting a removed member is a write
and a decision for an organization owner, so this script does not make it: it
establishes the removal from readable state and prints the request.

The point of the note: enabling required two-factor authentication does not
refuse non-compliant members, it removes them. The token keeps working, the
account stops being a member, and every private repository in the organization
answers 404 rather than 403.

What this can and cannot see: the current graph, not its history. Removal and
never having joined look identical from here; the audit log that records the
removal needs organization admin. So the finding is a cause and a motive rather
than a proof, and it is reported in those terms.

Environment:

    GITHUB_TOKEN    the token the failing integration holds
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_org_membership_lost")

API = "https://api.github.com"
UA = "github-org-membership-lost/1.0"

# GET /orgs/{org}/members/{username} answers with a status and no body. The 302
# is the one that matters and the one a default HTTP client hides.
MEMBERSHIP_STATUS = {
    204: "member",
    302: "requester-not-a-member",
    404: "not-a-member",
    403: "membership-unreadable",
}


def read_cost():
    """Requests this run will spend against the core quota. Pure."""
    return 4


def membership_state(status):
    """What one membership status means. Pure. (state, detail).

    The 302 is documented as "requester is not an organization member". When
    the requester and the subject are the same account, that redirect is the
    whole finding, and it is the reading a redirect-following client destroys.
    """
    code = int(status or 0)
    state = MEMBERSHIP_STATUS.get(code, "unclear")
    if state == "member":
        return (state, "204 means the account is a member of this organization.")
    if state == "requester-not-a-member":
        return (state, "the 302 says the account asking is not a member of the "
                       "organization. Asking about yourself, that is the removal.")
    if state == "not-a-member":
        return (state, "404 here means the requester is a member and the named "
                       "account is not.")
    if state == "membership-unreadable":
        return (state, "403 on the membership read itself. The credential "
                       "reached GitHub and was refused; sort that refusal first.")
    return ("unclear", "HTTP %s is not one of the documented answers for this "
                       "endpoint." % status)


def question_answered(followed_redirect):
    """Which question the status code is about. Pure. (state, detail).

    A followed 302 lands on the public-members endpoint, which asks whether a
    membership is publicly listed. That is a different property, and the answer
    looks exactly as authoritative as the one you wanted.
    """
    if followed_redirect:
        return ("public-membership-instead",
                "the client followed the redirect, so this answer came from "
                "the public members endpoint and is about whether membership "
                "is publicly listed. Send the call again with redirects off.")
    return ("membership",
            "redirects were disabled, so the status describes membership "
            "rather than public membership.")


def own_two_factor(user_payload):
    """Whether this account has 2FA on. Pure. True, False or None.

    None means unreadable: the field is only present on the authenticated
    user's own record and only when the token carries the user scope. Reporting
    an absent field as False would invent a compliance failure.
    """
    if not isinstance(user_payload, dict):
        return None
    if "two_factor_authentication" not in user_payload:
        return None
    value = user_payload.get("two_factor_authentication")
    return None if value is None else bool(value)


def requirement_state(org_payload):
    """Whether the org requires 2FA. Pure. True, False or None.

    None means unreadable, and the most common reason for that is the removal
    itself: the field is returned to callers with organization access, and
    being removed is what takes it away.
    """
    if not isinstance(org_payload, dict):
        return None
    if "two_factor_requirement_enabled" not in org_payload:
        return None
    value = org_payload.get("two_factor_requirement_enabled")
    return None if value is None else bool(value)


def listed_in_orgs(orgs, org):
    """Is the organization in GET /user/orgs. Pure.

    Weaker evidence than it looks. Without read:org a classic token sees only
    the organizations whose membership the account has made public, so absence
    here is corroboration and never the finding.
    """
    wanted = str(org or "").strip().lower()
    for entry in orgs or []:
        login = entry.get("login") if isinstance(entry, dict) else entry
        if str(login or "").strip().lower() == wanted:
            return True
    return False


def combine(membership, requirement, own_2fa):
    """Turn three readings into one finding. Pure. (state, detail)."""
    gone = membership in ("requester-not-a-member", "not-a-member")
    if gone and requirement is True:
        return ("not-a-member-2fa-required",
                "the account is not a member and the organization requires "
                "two-factor authentication. That is the cause and its motive. "
                "Removal and never having joined are indistinguishable through "
                "the API, so this is a finding to act on rather than a proof.")
    if gone and requirement is None:
        return ("not-a-member-motive-unreadable",
                "the account is not a member and the 2FA requirement could not "
                "be read. Reading it needs organization access, and losing that "
                "access is what this finding is.")
    if gone:
        return ("not-a-member-no-requirement",
                "the account is not a member and the organization does not "
                "require 2FA, so something else removed it. An owner can read "
                "the audit log; a read-only token cannot.")
    if membership == "member" and requirement is True and own_2fa is False:
        return ("member-at-risk",
                "still a member, the organization requires 2FA, and this "
                "account reports two-factor authentication off. That is a "
                "removal that has not happened yet.")
    if membership == "member" and requirement is True and own_2fa is None:
        return ("member-compliance-unreadable",
                "still a member of an organization that requires 2FA, and this "
                "token cannot read whether the account complies. The user scope "
                "is what exposes that field.")
    if membership == "member" and requirement is True:
        return ("member-compliant",
                "a member, the requirement is on, and this account has 2FA. "
                "Nothing here explains a 404.")
    if membership == "member":
        return ("member-no-requirement",
                "a member of an organization with no 2FA requirement. This "
                "note is not your problem; sort the 404 another way.")
    return ("membership-unreadable",
            "the membership question was not answered, so nothing can be "
            "concluded about a removal.")


def symptom(state):
    """What the integration is seeing, given the finding. Pure."""
    if state.startswith("not-a-member"):
        return ("every private repository in the organization answers 404, not "
                "403, because a non-member cannot see them at all. Public "
                "repositories keep answering, which is what makes the token "
                "look healthy.")
    if state == "member-at-risk":
        return ("nothing yet. The reads still work and will keep working until "
                "the requirement is enforced against this account.")
    return ("nothing that this note explains.")


def token_health(status):
    """State the credential's health explicitly. Pure. (state, detail)."""
    if int(status or 0) == 200:
        return ("healthy",
                "GET /user answered 200, so the credential authenticates. "
                "Nothing about the token explains what follows, and this line "
                "exists to end that search early.")
    if int(status or 0) == 401:
        return ("rejected",
                "401 means the credential itself was not accepted, which is a "
                "different note. This one starts from a token that works.")
    return ("unclear", "GET /user answered %s, which is neither of the two "
                       "cases this note starts from." % status)


def repair(state, org, login):
    """The request a human has to make. Pure. Nothing here is executed."""
    if state.startswith("not-a-member"):
        return ("enable 2FA on %s and ask an owner of %s to re-invite it, or "
                "replace the machine account with a GitHub App installation, "
                "which is not a member and is unaffected by member 2FA policy. "
                "Nothing here re-invites anybody." % (login, org))
    if state == "member-at-risk":
        return ("enable 2FA on %s now, before the requirement is enforced "
                "against it. Removal is silent when it comes." % login)
    if state == "member-compliance-unreadable":
        return ("read this with a token carrying the user scope, or check the "
                "account's security settings directly, to confirm it complies.")
    if state == "member-compliant":
        return ("nothing on membership. Take the 404 to the repository-level "
                "causes instead.")
    return ("answer the membership question first: send the members call with "
            "redirects disabled and read the status rather than the body.")


def get(session, path, allow_redirects=True):
    """One GET. Redirect following is a parameter because one call needs it off."""
    return session.get(API + path, timeout=30, allow_redirects=allow_redirects)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("org", help="the organization whose repositories 404")
    parser.add_argument("--login",
                        help="ask about this account instead of the token's own")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (the token the failing integration holds)")
        return 2

    log.info("read cost: %d request(s) against the core hourly quota", read_cost())

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    me = get(session, "/user")
    health, health_detail = token_health(me.status_code)
    payload = me.json() if me.status_code == 200 else {}
    login = args.login or payload.get("login") or "unknown"
    log.info("token: %s — %s", health, health_detail)
    if health != "healthy":
        return 2

    members = get(session, "/orgs/%s/members/%s" % (args.org, login),
                  allow_redirects=False)
    state, detail = membership_state(members.status_code)
    log.info("membership: GET /orgs/%s/members/%s -> HTTP %s (redirects disabled)",
             args.org, login, members.status_code)
    log.info("%s: %s", state, detail)
    asked, asked_detail = question_answered(False)
    log.info("question answered: %s. %s", asked, asked_detail)

    org_response = get(session, "/orgs/" + args.org)
    org_payload = org_response.json() if org_response.status_code == 200 else {}
    requirement = requirement_state(org_payload)
    log.info("motive: two_factor_requirement_enabled=%s",
             "unreadable" if requirement is None else requirement)

    orgs_response = get(session, "/user/orgs?per_page=100")
    orgs = orgs_response.json() if orgs_response.status_code == 200 else []
    listed = listed_in_orgs(orgs, args.org)
    log.info("corroboration: the organization is %s in GET /user/orgs, which "
             "without read:org only lists publicly-visible membership",
             "listed" if listed else "absent")

    own = own_two_factor(payload) if not args.login else None
    finding, finding_detail = combine(state, requirement, own)
    log.info("state: %s — %s", finding, finding_detail)
    log.info("symptom: %s", symptom(finding))
    log.info("repair: %s", repair(finding, args.org, login))

    print(json.dumps({
        "organization": args.org,
        "login": login,
        "token_health": health,
        "membership_status": members.status_code,
        "membership_state": state,
        "question_answered": asked,
        "two_factor_requirement_enabled": requirement,
        "account_two_factor": own,
        "listed_in_user_orgs": listed,
        "state": finding,
        "detail": finding_detail,
        "symptom": symptom(finding),
        "repair": repair(finding, args.org, login),
    }, indent=2, default=str))
    return 1 if finding.startswith("not-a-member") or finding == "member-at-risk" else 0


if __name__ == "__main__":
    sys.exit(main())
