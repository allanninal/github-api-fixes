"""Tell a token waiting for an owner apart from a token short a permission.

Read only. GET requests and nothing else, and a second promise this note needs
more than most: it never approves anything and never asks for approval. The
request this script detects already exists -- it was filed the moment the token
was created -- so there is nothing to resubmit, and resubmitting would only put
a duplicate into somebody's queue.

The diagnosis is a shape. A missing permission is endpoint-shaped: whatever the
token cannot do, it cannot do anywhere, including on repositories the account
owns outright. A pending organization approval is owner-shaped: every endpoint
family fails under one resource owner while personal reads succeed. Six cheap
reads separate them.

What this can and cannot see: it can prove the shape, and it can record that
neither of the neighbouring gates announced itself. It cannot read the
organization's token policy, and it cannot read the pending request with the
credential that is blocked by it -- that needs admin:org, which is why the
authoritative reading is optional here and belongs to the person who can also
end the wait.

Environment:

    GITHUB_TOKEN        the fine-grained token being refused
    GITHUB_ADMIN_TOKEN  an organization owner's credential, admin:org (optional)
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_pat_pending_approval")

API = "https://api.github.com"
UA = "github-pat-pending-approval/1.0"

SSO_HEADER = "x-github-sso"
ACCEPTED_PERMISSIONS_HEADER = "x-accepted-github-permissions"

TOKEN_PREFIXES = (
    ("github_pat_", "fine-grained PAT"),
    ("ghp_", "classic PAT"),
    ("gho_", "OAuth user token"),
    ("ghu_", "App user-to-server token"),
    ("ghs_", "App installation token"),
    ("ghr_", "App refresh token"),
)

# Two namespaces, several endpoint families each. The families matter more than
# the endpoints: the question is whether refusals cluster by owner or by
# family, and one endpoint cannot answer that.
PERSONAL_PROBES = (
    ("user", "/user"),
    ("repositories", "/user/repos?per_page=1"),
    ("issues", "/issues?per_page=1"),
)
ORG_PROBES = (
    ("repositories", "/orgs/%s/repos?per_page=1"),
    ("issues", "/orgs/%s/issues?per_page=1"),
    ("members", "/orgs/%s/members?per_page=1"),
)

REFUSED = (403, 404)

# Corroboration for ruling the neighbouring gate out, never for ruling this one
# in. The wording belongs to GitHub and can be edited at any time.
OAUTH_RESTRICTION_PHRASES = (
    "oauth app access restrictions",
    "third-party application",
)


def read_cost(with_admin=False):
    """Requests this run will spend against the core quota. Pure."""
    return len(PERSONAL_PROBES) + len(ORG_PROBES) + (1 if with_admin else 0)


def token_kind(token):
    """Name the credential from its prefix. Pure; nothing leaves the machine."""
    value = (token or "").strip()
    for prefix, name in TOKEN_PREFIXES:
        if value.startswith(prefix):
            return name
    return "unknown"


def probe_shape(personal, org):
    """Is this failure shaped like an owner or like an endpoint. Pure.

    personal, org: [(family, status), ...]. Returns (shape, detail).

    The refusal to answer on thin evidence is deliberate. One organization
    family failing tells you nothing about whether the gate is the owner, and a
    script that guesses there sends people to the wrong settings page.
    """
    if len(org) < 2:
        return ("insufficient-evidence",
                "fewer than two organization endpoint families were read, and "
                "one family cannot show whether refusals cluster by owner.")
    org_refused = [f for f, s in org if s in REFUSED]
    org_ok = [f for f, s in org if s == 200]
    personal_ok = [f for f, s in personal if s == 200]
    personal_refused = [f for f, s in personal if s in REFUSED]

    if not personal_ok:
        return ("credential-shaped",
                "nothing succeeded in the personal namespace either, so the "
                "credential itself is the thing to look at first.")
    if len(org_refused) == len(org) and not personal_refused:
        return ("owner-shaped",
                "every organization family is refused and no personal family "
                "is, so the gate is the resource owner and not any endpoint.")
    if org_ok and org_refused:
        shared = sorted(set(org_refused) & set(f for f, s in personal
                                               if s in REFUSED))
        if shared:
            return ("endpoint-shaped",
                    "the same family is refused in both namespaces (%s), which "
                    "is a permission the token does not hold rather than an "
                    "owner refusing it." % ", ".join(shared))
        return ("endpoint-shaped",
                "some organization families answer and others do not, so the "
                "owner is admitting this token and individual permissions are "
                "what is short.")
    if not org_refused:
        return ("nothing-refused",
                "every family answered in both namespaces, so nothing is "
                "waiting on anybody today.")
    return ("unclassified-shape",
            "the pattern does not match owner-shaped or endpoint-shaped; "
            "report the statuses rather than naming a cause.")


def header_is_not_the_discriminator():
    """The sentence that saves an hour. Pure."""
    return ("x-accepted-github-permissions describes what the endpoint accepts "
            "and never what the token holds, so it cannot settle this either "
            "way.")


def oauth_wording(message):
    """Did the refusal blame an OAuth App restriction. Pure."""
    text = str(message or "").lower()
    return any(p in text for p in OAUTH_RESTRICTION_PHRASES)


def classify(shape, kind, sso_seen, oauth_seen):
    """The verdict. Pure. (state, detail).

    The neighbouring gates are checked before the shape, because each of them
    announces itself and a diagnosis established by exclusion must never
    outrank one that was stated outright.
    """
    if kind != "fine-grained PAT":
        return ("not-a-fine-grained-token",
                "organization approval policy applies to fine-grained personal "
                "access tokens. A %s reaching this organization is governed by "
                "something else, with a different repair." % kind)
    if sso_seen:
        return ("saml-enforcement",
                "a refusal carried x-github-sso, so SAML enforcement is in play "
                "and that is a different note.")
    if oauth_seen:
        return ("oauth-app-restriction",
                "the refusal blamed OAuth App access restrictions, which govern "
                "applications rather than personal access tokens.")
    if shape == "owner-shaped":
        return ("pending-org-approval",
                "this token is waiting for an organization owner to approve it. "
                "Its permissions are held on paper and none in practice, which "
                "is why editing them changes nothing.")
    if shape == "endpoint-shaped":
        return ("permission-shaped",
                "the refusals follow an endpoint family rather than an owner, "
                "so this is a permission the token does not hold.")
    if shape == "credential-shaped":
        return ("credential-problem",
                "personal reads are failing too, so start with the credential.")
    if shape == "nothing-refused":
        return ("not-blocked", "nothing was refused during this run.")
    return ("undetermined",
            "not enough evidence to name a cause. Read more families before "
            "acting on this.")


def days_pending(created_at, now):
    """Whole days a request has been waiting. Pure."""
    if not created_at:
        return None
    text = str(created_at).strip().replace("Z", "+00:00")
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    if not when.tzinfo:
        when = when.replace(tzinfo=timezone.utc)
    return int((now - when).total_seconds() // 86400)


def find_request(requests_list, login):
    """The pending request filed by this account, if any. Pure.

    Matching is on the requester's login, which is public information and safe
    to print, unlike anything derived from the credential itself.
    """
    for item in requests_list or []:
        if not isinstance(item, dict):
            continue
        owner = item.get("owner") or {}
        if str(owner.get("login") or "").lower() == str(login or "").lower():
            return item
    return None


def repair(state, org):
    """The sentence a reader has to act on. Pure."""
    if state == "pending-org-approval":
        return ("an owner of %s approves the waiting request under the "
                "organization's personal access tokens settings. This script "
                "does not approve it and does not ask for it. Do not create a "
                "replacement token: the request already exists and a new one "
                "only queues behind it." % org)
    if state == "permission-shaped":
        return ("read x-accepted-github-permissions off the refusal, tick that "
                "permission on the token, and expect the organization to "
                "re-approve the change if it requires approval.")
    if state == "saml-enforcement":
        return "follow the SSO authorization URL on the refusal instead."
    if state == "oauth-app-restriction":
        return ("have an owner approve the application; this is a policy about "
                "an app rather than about a token.")
    if state == "not-a-fine-grained-token":
        return ("find the gate that applies to this credential type before "
                "looking at any approval queue.")
    if state == "credential-problem":
        return "fix the credential; no organization queue is involved yet."
    if state == "not-blocked":
        return "nothing. This token is reaching %s right now." % org
    return "read more endpoint families and run this again."


def get(session, url):
    """One GET. Returns the response object."""
    r = session.get(url, timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed "
                         "or revoked. That is a different note.")
    return r


def session_for(token):
    s = requests.Session()
    s.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })
    return s


def body_message(response):
    """The API's message string, if the body has one."""
    try:
        payload = response.json()
    except ValueError:
        return ""
    return (payload or {}).get("message", "") if isinstance(payload, dict) else ""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("org", help="the organization whose resources are refused")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (the fine-grained token being refused)")
        return 2
    admin = os.environ.get("GITHUB_ADMIN_TOKEN")

    log.info("read cost: %d request(s) against the core hourly quota%s",
             read_cost(bool(admin)),
             " including one read with the owner's credential" if admin else "")

    kind = token_kind(token)
    session = session_for(token)

    personal = []
    account = None
    for family, path in PERSONAL_PROBES:
        response = get(session, API + path)
        personal.append((family, response.status_code))
        if family == "user" and response.status_code == 200:
            account = (response.json() or {}).get("login")
    log.info("credential: %s, account=%s", kind, account or "unreadable")
    log.info("personal  %s", "  ".join("%s=%s" % (f, s) for f, s in personal))

    org_results = []
    sso_seen = False
    oauth_seen = False
    accepted_seen = None
    for family, template in ORG_PROBES:
        response = get(session, API + (template % args.org))
        org_results.append((family, response.status_code))
        if response.status_code in REFUSED:
            if response.headers.get(SSO_HEADER):
                sso_seen = True
            if oauth_wording(body_message(response)):
                oauth_seen = True
            accepted_seen = (response.headers.get(ACCEPTED_PERMISSIONS_HEADER)
                             or accepted_seen)
    log.info("org       %s", "  ".join("%s=%s" % (f, s) for f, s in org_results))

    shape, shape_detail = probe_shape(personal, org_results)
    log.info("shape: %s - %s", shape, shape_detail)
    log.info("%s: %s", SSO_HEADER,
             "present on a refusal" if sso_seen else "absent on every refusal")
    log.info("oauth restriction wording: %s",
             "present" if oauth_seen else "not present")
    log.info("note: %s", header_is_not_the_discriminator())

    state, detail = classify(shape, kind, sso_seen, oauth_seen)
    log.info("%s: %s", state, detail)

    pending = None
    waiting_days = None
    if admin:
        owner_session = session_for(admin)
        listing = get(owner_session,
                      API + "/orgs/%s/personal-access-token-requests?per_page=100"
                      % args.org)
        if listing.status_code == 200:
            body = listing.json()
            pending = find_request(body if isinstance(body, list) else [], account)
            if pending:
                waiting_days = days_pending(pending.get("created_at"),
                                            datetime.now(timezone.utc))
                log.info("pending request: filed %s day(s) ago by %s, "
                         "repository_selection=%s",
                         waiting_days, account,
                         pending.get("repository_selection"))
            else:
                log.info("pending request: none filed by %s is waiting, which "
                         "argues against this verdict", account)
        else:
            log.warning("personal-access-token-requests returned HTTP %s; that "
                        "endpoint needs admin:org", listing.status_code)

    log.info("repair: %s", repair(state, args.org))

    print(json.dumps({
        "organization": args.org,
        "account": account,
        "credential_kind": kind,
        "personal": dict(personal),
        "org": dict(org_results),
        "shape": shape,
        "shape_detail": shape_detail,
        "sso_header_seen": sso_seen,
        "oauth_wording_seen": oauth_seen,
        "accepted_permissions_header": accepted_seen,
        "accepted_permissions_note": header_is_not_the_discriminator(),
        "pending_request_found": bool(pending),
        "pending_request_days": waiting_days,
        "state": state,
        "detail": detail,
        "repair": repair(state, args.org),
    }, indent=2, default=str))
    return 1 if state == "pending-org-approval" else 0


if __name__ == "__main__":
    sys.exit(main())
