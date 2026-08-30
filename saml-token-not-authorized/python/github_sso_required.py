"""Tell a SAML refusal apart from every other 403, from one response header.

Read only. GET requests and nothing else, and one promise beyond that: this
script never authorizes anything. Authorizing a token against an organization
that enforces SAML single sign-on is deliberately a human step taken in a
browser, and a tool that performed it on your behalf would be a hole in the
control it is diagnosing. So this reads the refusal, prints the URL a person
has to visit, and stops there.

The finding is one response header. On a refusal, `x-github-sso` carries the
`required` form and a URL. On a successful cross-organization listing the same
header can carry the `partial-results` form instead, which is a different
problem with a different repair, and this script names it and hands it over
rather than flattening the two into one verdict.

What this can and cannot see: it can prove that this organization refuses this
credential and that GitHub attributed the refusal to SAML. It cannot tell you
whether the token was authorized once and lapsed, because a read-only token
cannot read its own authorization history; that needs an organization owner and
is the sibling note. Pass --worked-before if you know it used to succeed and
this script will send you there instead of offering a first-time repair.

Environment:

    GITHUB_TOKEN    the token that is being refused
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_sso_required")

API = "https://api.github.com"
UA = "github-sso-required/1.0"

SSO_HEADER = "x-github-sso"

# The two forms, and they are opposites. `required` arrives on a response that
# returned nothing; `partial-results` arrives on a 200 that returned most of
# something. Testing for the header without reading the form gets one of them
# exactly backwards.
FORM_REQUIRED = "required"
FORM_PARTIAL = "partial-results"

# Longest prefixes first so a future prefix extending an existing one is not
# swallowed by its shorter neighbour.
TOKEN_PREFIXES = (
    ("github_pat_", "fine-grained PAT"),
    ("ghp_", "classic PAT"),
    ("gho_", "OAuth user token"),
    ("ghu_", "App user-to-server token"),
    ("ghs_", "App installation token"),
    ("ghr_", "App refresh token"),
)

# Whether a click on the authorization URL can help this kind of credential.
# Getting this wrong costs a day, because the repair printed for one kind is
# useless and misleading for another.
CLICK_HELPS = {
    "classic PAT": (True, "a classic PAT is authorized per token, per "
                          "organization, by a person. Reminting it wider "
                          "cannot change this answer."),
    "OAuth user token": (True, "an OAuth token is authorized per token, per "
                               "organization, by the person who granted it."),
    "App user-to-server token": (True, "a user-to-server token inherits the "
                                       "user's SAML standing, so the same "
                                       "click applies to it."),
    "fine-grained PAT": (False, "a fine-grained PAT has no per-token SSO "
                                "authorization page. Its access to an "
                                "organization is settled at creation and by "
                                "the organization's token policy, so a refusal "
                                "here is usually a token waiting for an owner "
                                "to approve it."),
    "App installation token": (False, "an installation token is not subject to "
                                      "per-token SSO authorization at all. If "
                                      "one is being refused, SAML is not the "
                                      "reason and this note is the wrong one."),
    "App refresh token": (False, "a refresh token is not used against these "
                                 "endpoints; exchange it first."),
    "unknown": (False, "the credential type could not be named from its "
                       "prefix, so nothing here prices whether a click helps."),
}

STABLE_SSO_URL = "https://github.com/orgs/%s/sso"


def read_cost():
    """Requests this run will spend against the core quota. Pure."""
    return 3


def token_kind(token):
    """Name the credential from its prefix. Pure; nothing leaves the machine."""
    value = (token or "").strip()
    for prefix, name in TOKEN_PREFIXES:
        if value.startswith(prefix):
            return name
    return "unknown"


def parse_sso_header(value):
    """Split x-github-sso into a form and its parameters. Pure.

    Returns {"form": str|None, "url": str|None, "organizations": [str]}. The
    header is `form; key=value; key=value`, and the value of `url` contains its
    own `=` characters, so the split is on the first one only.
    """
    out = {"form": None, "url": None, "organizations": []}
    if not value:
        return out
    parts = [p.strip() for p in str(value).split(";") if p.strip()]
    if not parts:
        return out
    out["form"] = parts[0].lower()
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, _, val = part.partition("=")
        key, val = key.strip().lower(), val.strip()
        if key == "url":
            out["url"] = val
        elif key == "organizations":
            out["organizations"] = [i.strip() for i in val.split(",") if i.strip()]
    return out


def enforcement_signature(meta_status, listing_status, sso):
    """Classify one pair of organization reads plus the header. Pure.

    (state, detail). The pair matters: a misspelled organization fails both
    reads, a dead credential fails everything, and SAML enforcement is the case
    where the public metadata is readable and the listing is not.
    """
    form = (sso or {}).get("form")
    refused = listing_status in (403, 404)
    if form == FORM_PARTIAL:
        return ("partial-results-not-a-refusal",
                "the header carries the partial-results form, which arrives on "
                "a response that succeeded with organizations left out of it. "
                "That is a different problem: nothing was refused here.")
    if refused and form == FORM_REQUIRED:
        return ("sso-authorization-required",
                "this organization enforces SAML single sign-on and this "
                "credential has not been authorized against it. The token is "
                "valid; the organization has not admitted it.")
    if refused and meta_status == 200:
        return ("refused-without-sso-header",
                "the organization is readable and the listing is not, but "
                "GitHub did not attribute the refusal to SAML. Look at the "
                "scopes the endpoint accepts, or at an organization policy "
                "that blocks the application rather than the token.")
    if refused:
        return ("organization-unreadable",
                "even the organization's own record could not be read, so this "
                "may be a name that does not resolve rather than a gate. Check "
                "the spelling before reading anything else into it.")
    if form == FORM_REQUIRED:
        return ("sso-required-on-a-success",
                "the listing succeeded and still carried the required form. "
                "Treat the header as advance warning: another endpoint on this "
                "organization will refuse the same credential.")
    return ("no-refusal-to-explain",
            "the listing succeeded and carried no SAML header, so this "
            "credential is authorized for this organization right now.")


def authorize_url(sso, org):
    """The address a person has to open. Pure. (url, source).

    The header's URL carries a short-lived authorization_request identifier, so
    a URL copied into a ticket last week is already stale. The stable address
    reaches the same page and never expires, which is the one worth printing
    when the header did not supply one.
    """
    from_header = (sso or {}).get("url")
    if from_header:
        return (from_header, "from the x-github-sso header, and short-lived: "
                             "treat it as good for about an hour")
    return (STABLE_SSO_URL % org,
            "the stable organization address, because the refusal carried no "
            "URL of its own")


def click_verdict(kind):
    """Can a human authorization click change this answer. Pure. (bool, detail)."""
    return CLICK_HELPS.get(kind, CLICK_HELPS["unknown"])


def which_sso_note(worked_before):
    """First authorization, or a lapsed one. Pure. (state, detail).

    A read-only token cannot read its own authorization history, so the one
    fact that separates these is the caller's own: did this credential ever
    succeed against this organization. Asked rather than guessed.
    """
    if worked_before:
        return ("session-lapse",
                "this credential succeeded here before, so it was authorized "
                "once and the authorization has lapsed rather than never "
                "existing. The click is the same; what changes is that it will "
                "be needed again on the organization's schedule.")
    return ("first-authorization",
            "no prior success was reported, so treat this as a credential that "
            "has never been authorized for this organization. One click "
            "settles it until the organization's session interval says "
            "otherwise.")


def repair(state, org, url, kind, worked_before):
    """The sentence a reader has to act on. Pure."""
    helps, _detail = click_verdict(kind)
    if state != "sso-authorization-required":
        if state == "partial-results-not-a-refusal":
            return ("read the withheld organization IDs out of the header and "
                    "treat the response as incomplete. Nothing here needs "
                    "authorizing to make a call succeed, because the call "
                    "succeeded.")
        if state == "refused-without-sso-header":
            return ("diff the scopes the refusal names against the ones the "
                    "credential holds, and check whether the organization "
                    "restricts the application itself.")
        if state == "organization-unreadable":
            return "check the organization name, then read this again."
        return "nothing on SAML. This credential is admitted to %s today." % org
    if not helps:
        return ("do not send anyone to the SSO page for this credential type. "
                "The refusal is real and SAML is not the explanation for it.")
    lead = ("open %s in a browser and authorize this credential for %s. This "
            "script does not open it and must not: the click is the control."
            % (url, org))
    if worked_before:
        return (lead + " Expect to do it again whenever the organization's "
                       "SAML session lapses, and move anything unattended onto "
                       "an App installation token, which does not lapse with a "
                       "person's session.")
    return (lead + " For anything unattended, prefer an App installation "
                   "token: it belongs to an installation the organization "
                   "already approved and is never subject to this click.")


def get(session, path):
    """One GET. Returns the response object."""
    r = session.get(API + path, timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed "
                         "or revoked. That is a different note; this one starts "
                         "from a credential GitHub still recognises.")
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("org", help="the organization login being refused")
    ap.add_argument("--worked-before", action="store_true",
                    help="this credential succeeded against this organization "
                         "in the past, which makes it a lapse rather than a "
                         "first authorization")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    log.info("read cost: %d request(s) against the core hourly quota", read_cost())

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })

    kind = token_kind(token)
    me = get(session, "/user")
    account = (me.json() or {}).get("login") if me.status_code == 200 else None
    log.info("token: %s, account=%s", kind, account or "unreadable")

    meta = get(session, "/orgs/" + args.org)
    log.info("GET /orgs/%s -> %s", args.org, meta.status_code)

    listing = get(session, "/orgs/%s/repos?per_page=1" % args.org)
    log.info("GET /orgs/%s/repos -> %s", args.org, listing.status_code)

    raw = listing.headers.get(SSO_HEADER) or meta.headers.get(SSO_HEADER)
    sso = parse_sso_header(raw)
    log.info("%s: form=%s", SSO_HEADER, sso["form"] or "absent")

    state, detail = enforcement_signature(meta.status_code, listing.status_code, sso)
    log.info("%s: %s", state, detail)

    helps, click_detail = click_verdict(kind)
    log.info("credential: %s", click_detail)

    url, url_source = authorize_url(sso, args.org)
    history_state, history_detail = which_sso_note(args.worked_before)
    if state == "sso-authorization-required":
        log.info("authorization url: %s (%s)", url, url_source)
        log.info("%s: %s", history_state, history_detail)
    log.info("repair: %s", repair(state, args.org, url, kind, args.worked_before))

    print(json.dumps({
        "organization": args.org,
        "account": account,
        "token_kind": kind,
        "org_read_status": meta.status_code,
        "listing_status": listing.status_code,
        "sso_header": sso,
        "state": state,
        "detail": detail,
        "click_can_help": helps,
        "authorization_url": url if state == "sso-authorization-required" else None,
        "history_state": history_state,
        "repair": repair(state, args.org, url, kind, args.worked_before),
    }, indent=2, default=str))
    return 1 if state == "sso-authorization-required" else 0


if __name__ == "__main__":
    sys.exit(main())
