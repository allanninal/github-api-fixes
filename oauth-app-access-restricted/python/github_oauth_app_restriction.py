"""Show that an organization is refusing an application rather than a token.

Read only. GET requests and nothing else, and it approves nothing: approving an
OAuth App for an organization is an owner's decision made in the organization's
settings, there is no API that performs it, and this script neither asks for it
nor pretends to.

The verdict is a behavioural shape plus two absences. One token reads two
namespaces, personal and organization; a refusal on the second while the first
succeeds is the shape. The refusal carrying no x-github-sso header is the first
absence, and it is what separates an application-level policy from SAML
enforcement. The lack of any endpoint that publishes the policy is the second,
which is why the message string is scored as corroboration rather than proof.

What this can and cannot see: it can prove that this organization refuses this
credential where an anonymous caller succeeds, and that GitHub did not
attribute the refusal to SAML. It cannot read the organization's OAuth App
policy, because that needs owner access, and it cannot be run usefully by the
application's author at all -- the policy is invisible from the app's side. Run
it with a token issued to the application, held by a member of the
organization.

Environment:

    GITHUB_TOKEN    a token issued by the OAuth App, held by an org member
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_oauth_app_restriction")

API = "https://api.github.com"
UA = "github-oauth-app-restriction/1.0"

SSO_HEADER = "x-github-sso"
ACCEPTED_SCOPES_HEADER = "x-accepted-oauth-scopes"

TOKEN_PREFIXES = (
    ("github_pat_", "fine-grained PAT"),
    ("ghp_", "classic PAT"),
    ("gho_", "OAuth user token"),
    ("ghu_", "App user-to-server token"),
    ("ghs_", "App installation token"),
    ("ghr_", "App refresh token"),
)

# Credentials this policy can actually govern. It restricts OAuth Apps, so a
# token issued by one is in scope and the rest are refused, when they are
# refused, by other mechanisms with other repairs.
GOVERNED_BY_OAUTH_POLICY = {
    "OAuth user token": True,
    "unknown": True,
    "classic PAT": False,
    "fine-grained PAT": False,
    "App user-to-server token": False,
    "App installation token": False,
    "App refresh token": False,
}

# Corroboration only. This is prose written by GitHub and it can be reworded
# without warning, so matching it raises confidence and never decides the
# verdict on its own.
RESTRICTION_PHRASES = (
    "oauth app access restrictions",
    "oauth application access restrictions",
    "third-party application",
    "has not been granted access",
)


def read_cost():
    """Authenticated requests this run spends. Pure.

    The anonymous read is deliberately not counted here: it draws on the
    separate 60-an-hour unauthenticated bucket rather than on core quota.
    """
    return 3


def token_kind(token):
    """Name the credential from its prefix. Pure; nothing leaves the machine."""
    value = (token or "").strip()
    for prefix, name in TOKEN_PREFIXES:
        if value.startswith(prefix):
            return name
    return "unknown"


def governed(kind):
    """Can this policy apply to this credential at all. Pure. (bool, detail)."""
    if GOVERNED_BY_OAUTH_POLICY.get(kind, True):
        return (True, "this policy governs tokens issued by an OAuth App, and "
                      "a %s is one of those." % kind)
    return (False, "a %s is not issued by an OAuth App, so this policy does not "
                   "govern it. A refusal here has another cause and another "
                   "note." % kind)


def message_signature(message):
    """Score the refusal's prose. Pure. (matched, phrase or None).

    Deliberately a list of substrings rather than one exact sentence, because
    the wording is not an API contract and a diagnostic that hangs on it breaks
    silently the day it is edited.
    """
    text = str(message or "").lower()
    for phrase in RESTRICTION_PHRASES:
        if phrase in text:
            return (True, phrase)
    return (False, None)


def namespace_shape(personal_status, org_status):
    """The two-namespace reading. Pure. (state, detail)."""
    personal_ok = personal_status == 200
    org_refused = org_status in (403, 404)
    if personal_ok and org_refused:
        return ("personal-ok-org-refused",
                "the same token reads personal repositories and is refused on "
                "this organization, which is a gate around the organization "
                "rather than a problem with the credential.")
    if not personal_ok and org_refused:
        return ("refused-everywhere",
                "the token is refused on personal repositories too, so this is "
                "the credential rather than any organization policy.")
    if personal_ok and not org_refused:
        return ("nothing-refused",
                "both namespaces answered, so nothing is being restricted for "
                "this application today.")
    return ("unclassified-shape",
            "the pair of reads does not match a shape this script knows how to "
            "name; report both statuses rather than guessing.")


def anonymous_contrast(anon_status, token_status):
    """Compare a credentialled read against no credential at all. Pure."""
    if anon_status == 200 and token_status in (403, 404):
        return ("restricted-below-anonymous",
                "this token is refused where no token at all succeeds, so it is "
                "being blocked rather than being under-privileged.")
    if anon_status in (403, 404) and token_status in (403, 404):
        return ("private-to-everyone",
                "an anonymous caller cannot see this organization's listing "
                "either, so the contrast proves nothing here. The organization "
                "may simply have no public repositories.")
    return ("no-contrast",
            "the authenticated read succeeded, so there is nothing to contrast.")


def discriminate(shape, sso_form, accepted_scopes, matched, kind):
    """The verdict. Pure. (state, detail).

    Order matters. SAML is checked first because its header is unambiguous, the
    scope header second because it names its own repair, and the OAuth
    restriction last because it is the diagnosis of exclusion -- established by
    a shape and by what is missing.
    """
    ok, _detail = governed(kind)
    if sso_form:
        return ("saml-not-oauth-restriction",
                "the refusal carries x-github-sso, so this is SAML enforcement "
                "and not an application policy. Two different notes, and the "
                "header settles which.")
    if accepted_scopes:
        return ("scope-shaped-refusal",
                "the refusal names the scopes it accepts in "
                "x-accepted-oauth-scopes, which an application restriction does "
                "not do. Diff that against what the token holds first.")
    if shape == "refused-everywhere":
        return ("credential-problem",
                "the token is refused in its own namespace, so nothing about "
                "an organization's policy explains it.")
    if shape == "nothing-refused":
        return ("not-restricted",
                "this application is reaching the organization's resources "
                "right now.")
    if shape != "personal-ok-org-refused":
        return ("undetermined",
                "the reads do not form a shape this script will put a name to.")
    if not ok:
        return ("not-an-oauth-app-credential",
                "the shape is right but the credential is not one this policy "
                "governs, so look for an organization gate that applies to this "
                "credential type instead.")
    if matched:
        return ("oauth-app-restricted",
                "this organization restricts which OAuth Apps may access its "
                "data and this application has not been approved. No scope, no "
                "reissued token and no other user account will change that.")
    return ("oauth-app-restricted-likely",
            "the shape is exactly an application restriction and the refusal's "
            "wording did not match anything known, which happens when GitHub "
            "rewords a message. Treat the shape as the finding and the wording "
            "as unavailable corroboration.")


def visibility_note():
    """Who can and cannot run this diagnosis. Pure."""
    return ("the application's author cannot see this policy from their side. "
            "This run needs a token issued to the app, held by a member of the "
            "organization.")


def repair(state, org):
    """The sentence a reader has to act on. Pure."""
    if state in ("oauth-app-restricted", "oauth-app-restricted-likely"):
        return ("an owner of %s approves the application in the organization's "
                "third-party access settings. There is no API that grants it "
                "and this script does not ask for it. Structurally, a GitHub "
                "App is installed per account rather than approved by blanket "
                "policy, which removes this failure mode." % org)
    if state == "saml-not-oauth-restriction":
        return ("authorize the credential for the organization through the URL "
                "in the x-github-sso header; that is a different repair.")
    if state == "scope-shaped-refusal":
        return ("compare the accepted scopes against the ones the token holds "
                "and mint the narrowest one that closes the gap.")
    if state == "credential-problem":
        return ("fix the credential first; no organization policy is in play "
                "while personal reads are failing too.")
    if state == "not-an-oauth-app-credential":
        return ("find the gate that applies to this credential type. An OAuth "
                "App policy is not it.")
    if state == "not-restricted":
        return "nothing. This application is not being restricted by %s." % org
    return ("report both statuses and the headers; this run did not reach a "
            "verdict worth acting on.")


def get(session, url):
    """One GET. Returns the response object."""
    r = session.get(url, timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed "
                         "or revoked. That is a different note.")
    return r


def body_message(response):
    """The API's message string, if the body has one. Pure enough."""
    try:
        payload = response.json()
    except ValueError:
        return ""
    return (payload or {}).get("message", "") if isinstance(payload, dict) else ""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("org", help="the organization refusing the application")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token issued by the app)")
        return 2

    log.info("read cost: %d request(s) against the core hourly quota, plus 1 "
             "unauthenticated request against the separate 60-an-hour "
             "anonymous bucket", read_cost())

    common = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    }
    session = requests.Session()
    session.headers.update(dict(common, Authorization="Bearer " + token))

    kind = token_kind(token)
    me = get(session, API + "/user")
    account = (me.json() or {}).get("login") if me.status_code == 200 else None
    log.info("credential: %s, account=%s", kind, account or "unreadable")

    personal = get(session, API + "/user/repos?per_page=1")
    log.info("GET /user/repos -> %s", personal.status_code)

    org_listing = get(session, API + "/orgs/%s/repos?per_page=1" % args.org)
    log.info("GET /orgs/%s/repos -> %s", args.org, org_listing.status_code)

    sso_form = (org_listing.headers.get(SSO_HEADER) or "").split(";")[0].strip().lower()
    accepted = org_listing.headers.get(ACCEPTED_SCOPES_HEADER)
    log.info("%s: %s", SSO_HEADER,
             sso_form or "absent, and that absence is the finding: a SAML "
                         "refusal always carries this header")
    matched, phrase = message_signature(body_message(org_listing))
    log.info("message: %s", "matched the documented OAuth App restriction "
                            "wording" if matched else "did not match any known "
                            "restriction wording")

    anonymous = requests.Session()
    anonymous.headers.update(common)
    anon = anonymous.get(API + "/orgs/%s/repos?per_page=1" % args.org, timeout=30)
    log.info("anonymous read of the same listing -> %s", anon.status_code)
    contrast_state, contrast_detail = anonymous_contrast(
        anon.status_code, org_listing.status_code)
    log.info("%s: %s", contrast_state, contrast_detail)

    shape, shape_detail = namespace_shape(personal.status_code,
                                          org_listing.status_code)
    log.info("%s: %s", shape, shape_detail)

    state, detail = discriminate(shape, sso_form, accepted, matched, kind)
    log.info("%s: %s", state, detail)
    log.info("visibility: %s", visibility_note())
    log.info("repair: %s", repair(state, args.org))

    print(json.dumps({
        "organization": args.org,
        "account": account,
        "credential_kind": kind,
        "personal_status": personal.status_code,
        "org_status": org_listing.status_code,
        "anonymous_status": anon.status_code,
        "sso_header": sso_form or None,
        "accepted_scopes_header": accepted,
        "message_matched": matched,
        "message_phrase": phrase,
        "shape": shape,
        "contrast": contrast_state,
        "state": state,
        "detail": detail,
        "visibility": visibility_note(),
        "repair": repair(state, args.org),
    }, indent=2, default=str))
    return 1 if state.startswith("oauth-app-restricted") else 0


if __name__ == "__main__":
    sys.exit(main())
