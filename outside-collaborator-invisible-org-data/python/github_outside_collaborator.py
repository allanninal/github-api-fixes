"""Tell an outside collaborator from a member with a narrow token.

Read only. Four cheap GETs. Nothing is invited, added or promoted: making an
account an organization member is a decision about who is inside a company's
organization, and this script prints the request rather than making it.

The point of the note: an outside collaborator holds specific repositories
inside an organization without being in the organization. Repository reads
work, organization reads do not, and no scope closes the gap because a scope
bounds what a token may do on the account's behalf rather than granting the
account a relationship.

What this can and cannot see: GET /orgs/{org}/outside_collaborators names the
condition outright and needs organization read access, which is exactly what
this account lacks. So the diagnosis is made from the token's own side, by
partitioning its repositories by the affiliation that reached them.

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
log = logging.getLogger("github_outside_collaborator")

API = "https://api.github.com"
UA = "github-outside-collaborator/1.0"

# The three ways GET /user/repos says an account reached a repository.
AFFILIATIONS = ("owner", "collaborator", "organization_member")


def read_cost(with_org_probe):
    """REST requests this run will spend. Pure. Printed before any are spent."""
    return 3 + (1 if with_org_probe else 0)


def header_value(headers, name):
    """Case-insensitive header read against a plain dict. Pure."""
    if not isinstance(headers, dict):
        return None
    wanted = str(name).lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return value
    return None


def has_next_page(link_header):
    """Is there another page after this one. Pure. No regular expression.

    Used to turn a count into a floor. A number that admits it is a lower bound
    is useful; one that quietly truncates is the same bug this note is about,
    committed by the diagnostic instead of the integration.
    """
    for part in str(link_header or "").split(","):
        if 'rel="next"' in part.replace("'", '"').replace(" ", ""):
            return True
        if 'rel="next"' in part:
            return True
    return False


def sso_reading(headers):
    """Is this response's shortness announced. Pure. (state, detail).

    Read so the script can say "this is not the SAML note" with evidence. That
    note owns organization data withheld from a 200 and announced in a header;
    the case here announces nothing at all.
    """
    value = header_value(headers, "x-github-sso")
    if not value:
        return ("no-sso-header",
                "no partial-results header accompanied this list, so nothing "
                "was announced as withheld. The SAML note is about the case "
                "where GitHub does tell you.")
    if "partial-results" in str(value):
        return ("sso-partial-results",
                "this list is explicitly incomplete: GitHub withheld "
                "organizations this token is not SSO-authorized for and said "
                "so in the header. That is a different note, and any "
                "membership conclusion from this list is unsafe.")
    return ("sso-header-present",
            "an SSO header came back without a partial-results marker. Nothing "
            "is stated as withheld, but treat the list with care.")


def is_member(orgs, org):
    """Does GET /user/orgs list this organization. Pure."""
    wanted = str(org or "").lower()
    for entry in orgs or []:
        if isinstance(entry, dict) and str(entry.get("login") or "").lower() == wanted:
            return True
    return False


def repos_in_org(repos, org):
    """Full names of the repositories in this organization. Pure."""
    wanted = str(org or "").lower()
    out = []
    for repo in repos or []:
        if not isinstance(repo, dict):
            continue
        owner = str(((repo.get("owner") or {}).get("login")) or "").lower()
        if owner == wanted:
            out.append(repo.get("full_name"))
    return out


def counted(names, more_pages):
    """A count, honest about being a floor. Pure. (count, exact, phrase)."""
    total = len(names or [])
    if more_pages:
        return (total, False, "at least %d" % total)
    return (total, True, str(total))


def role_verdict(member, collaborator_count, member_affiliated_count):
    """Which relationship the account has. Pure. (state, detail).

    Four states, and three of them send you somewhere else. The one this note
    owns is the account with repositories in the organization and no membership.
    """
    if member and member_affiliated_count > 0:
        return ("organization-member",
                "the organization is in this account's membership list and its "
                "repositories arrive under organization_member. Whatever is "
                "failing, it is not this.")
    if member and member_affiliated_count == 0:
        return ("member-with-no-implicit-repos",
                "the account is a member and reaches no repository through "
                "that membership. That is what a base permission of none looks "
                "like organization-wide, and it has its own note.")
    if not member and collaborator_count > 0:
        return ("outside-collaborator",
                "repositories inside the organization, no standing in the "
                "organization. No scope grants standing, which is why widening "
                "the token changes nothing.")
    return ("no-relationship",
            "not a member and no repositories in this organization reachable "
            "as a collaborator. An account that used to have access and now "
            "has none is a removal rather than a role, and that has its own "
            "note.")


def org_endpoint_expectation(role):
    """What organization endpoints will do for this role. Pure. dict.

    The second entry is the one worth carrying back to the code: it does not
    fail, it under-reports.
    """
    if role == "organization-member":
        return {"members-and-teams": "answer for a member",
                "org-repos-listing": "returns the repositories a member may see",
                "outside-collaborators-listing": "needs organization read access"}
    return {
        "members-and-teams": "refuse a non-member, and 404 rather than 403 so "
                             "nothing is confirmed to exist",
        "org-repos-listing": "answers 200 and returns the public repositories "
                             "only. This does not fail; it under-reports, with "
                             "no header and no error.",
        "outside-collaborators-listing": "names this condition outright and "
                                         "needs organization read access, "
                                         "which this account does not have",
    }


def token_class_caveat(token):
    """A documented gap that can invert the diagnosis. Pure. (state, detail)."""
    value = (token or "").strip()
    if value.startswith("github_pat_"):
        return ("fine-grained-gap",
                "GitHub documents, among the things fine-grained tokens cannot "
                "yet do, contributing to repositories where the user is an "
                "outside or repository collaborator. If a classic token works "
                "where this one does not, that inversion is evidence of the "
                "role rather than a bug in your code.")
    if value.startswith("ghp_"):
        return ("classic-token",
                "a classic token is not subject to the documented fine-grained "
                "gap for outside collaborators, so a difference between the "
                "two classes is worth testing before blaming anything else.")
    return ("class-not-recognised",
            "the credential class could not be named from its prefix, so the "
            "fine-grained caveat cannot be applied either way.")


def org_probe_reading(repo_status, org_status):
    """One repository read against one organization read. Pure. (state, detail)."""
    repo = None if repo_status is None else int(repo_status)
    org = None if org_status is None else int(org_status)
    if org is None:
        return ("org-not-probed",
                "no organization endpoint was probed, so the partition is the "
                "only evidence here.")
    if repo is not None and repo == 200 and org == 404:
        return ("repo-yes-org-no",
                "a repository in the organization answers and an organization "
                "endpoint does not. That pair is the sentence to put in the "
                "ticket.")
    if org in (200, 204):
        return ("org-reachable",
                "the organization endpoint answered, so membership is not what "
                "is missing.")
    if org in (401, 403):
        return ("org-refused-not-hidden",
                "a refusal rather than a 404 points at a credential or a "
                "policy rather than at membership. Sort that first.")
    return ("org-probe-inconclusive",
            "the pair of statuses does not describe a membership problem.")


def verdict(role, sso_state):
    """The finding, in one state. Pure. (state, detail)."""
    if sso_state == "sso-partial-results":
        return ("membership-list-incomplete",
                "the organization list this conclusion would rest on is "
                "explicitly partial, so no membership answer from it can be "
                "trusted. Authorize the token for SSO and re-run.")
    return (role,
            "this is the relationship the readings describe." if role
            else "no relationship could be determined.")


def repair(state, org, login):
    """The sentence a reader has to act on. Pure. Nothing here invites anybody."""
    if state == "outside-collaborator":
        return ("either ask an owner of %s to add %s as a member with an "
                "appropriate role, which is a change to who is inside that "
                "organization, or drop the organization-level calls and work "
                "at repository scope where this account's access actually is. "
                "Nothing here invites anybody." % (org, login))
    if state == "member-with-no-implicit-repos":
        return ("read the organization's default repository permission before "
                "anything else; an organization-wide default of none produces "
                "exactly this and has its own note.")
    if state == "no-relationship":
        return ("find out whether this account was removed from the "
                "organization rather than never added. A removal leaves a "
                "healthy token with no access at all.")
    if state == "membership-list-incomplete":
        return ("authorize this token for the organization's SSO and re-run. "
                "Until then the membership list is not evidence.")
    return "nothing to repair from this reading."


def get(session, path):
    """One GET. Returns the response object."""
    return session.get(API + path, timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("org", help="the organization whose data is invisible")
    parser.add_argument("--org-probe", action="store_true",
                        help="also read one organization-level endpoint")
    parser.add_argument("--repo",
                        help="a repository in that org to pair the probe with, "
                             "as owner/name")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (the token the failing integration holds)")
        return 2

    log.info("read cost: %d REST request(s) against the core hourly quota",
             read_cost(args.org_probe))

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub refuses requests with no User-Agent before it looks at auth.
        "User-Agent": UA,
    })

    class_state, class_detail = token_class_caveat(token)
    log.info("%s: %s", class_state, class_detail)

    me = get(session, "/user")
    login = (me.json() or {}).get("login") if me.status_code == 200 else None
    log.info("identity: %s", login or "unreadable")

    orgs_response = get(session, "/user/orgs?per_page=100")
    orgs = orgs_response.json() if orgs_response.status_code == 200 else []
    sso_state, sso_detail = sso_reading(dict(orgs_response.headers))
    member = is_member(orgs, args.org)
    log.info("membership: %s is %sin GET /user/orgs. %s", args.org,
             "" if member else "not ", sso_detail)

    partition = {}
    for affiliation in ("collaborator", "organization_member"):
        response = get(session, "/user/repos?affiliation=%s&per_page=100"
                       % affiliation)
        names = repos_in_org(response.json() if response.status_code == 200 else [],
                             args.org)
        more = has_next_page(header_value(dict(response.headers), "link"))
        total, exact, phrase = counted(names, more)
        partition[affiliation] = {"count": total, "exact": exact,
                                  "phrase": phrase, "names": names[:20]}
    log.info("affiliation partition: %s repo(s) in %s reached as collaborator, "
             "%s reached as organization_member",
             partition["collaborator"]["phrase"], args.org,
             partition["organization_member"]["phrase"])

    org_status, repo_status = None, None
    if args.org_probe:
        org_status = get(session, "/orgs/%s/members?per_page=1" % args.org).status_code
        log.info("org probe: GET /orgs/%s/members?per_page=1 -> HTTP %s",
                 args.org, org_status)
    if args.repo:
        repo_status = get(session, "/repos/%s" % args.repo).status_code
        log.info("repo probe: GET /repos/%s -> HTTP %s", args.repo, repo_status)
    probe_state, probe_detail = org_probe_reading(repo_status, org_status)
    log.info("%s: %s", probe_state, probe_detail)

    role, role_detail = role_verdict(member,
                                     partition["collaborator"]["count"],
                                     partition["organization_member"]["count"])
    log.info("%s: %s", role, role_detail)

    expectation = org_endpoint_expectation(role)
    log.info("quiet-failure-ahead: %s", expectation["org-repos-listing"])

    state, detail = verdict(role, sso_state)
    fix = repair(state, args.org, login or "this account")
    log.info("repair: %s", fix)

    print(json.dumps({
        "organization": args.org,
        "login": login,
        "is_member": member,
        "sso_state": sso_state,
        "affiliation_partition": partition,
        "org_probe_status": org_status,
        "repo_probe_status": repo_status,
        "probe_state": probe_state,
        "token_class_state": class_state,
        "org_endpoint_expectation": expectation,
        "state": state,
        "detail": detail,
        "repair": fix,
    }, indent=2, default=str))
    return 1 if state in ("outside-collaborator", "member-with-no-implicit-repos",
                          "no-relationship", "membership-list-incomplete") else 0


if __name__ == "__main__":
    sys.exit(main())
