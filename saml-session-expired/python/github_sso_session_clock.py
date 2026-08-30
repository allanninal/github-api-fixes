"""Read the expiry on a SAML credential authorization before it lapses.

Read only, and it authorizes nothing. The repair for a lapsed SAML session is a
person re-authenticating in a browser; this script reports the date that will
become necessary and never performs it.

Two credentials, on purpose. GITHUB_TOKEN is the credential in trouble.
GITHUB_ADMIN_TOKEN belongs to an organization owner and is the only one that
can read GET /orgs/{org}/credential-authorizations, where the dated record
lives. The credential being diagnosed is not permitted to know its own expiry,
which is the reason this note needs a second reader at all.

The match on token_last_eight happens in memory and those characters are never
logged or serialised. Eight characters of a live credential are still part of a
live credential, and they are enough to correlate one across systems.

What this can and cannot see: it can read this grant's expiry, when the
credential was last used, and therefore whether a refusal is a lapse or a
credential that was never authorized. It cannot read the organization's
re-authentication interval, which is not published by the API, so the cadence is
reported as inferred rather than measured.

Environment:

    GITHUB_TOKEN        the credential being diagnosed
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
log = logging.getLogger("github_sso_session_clock")

API = "https://api.github.com"
UA = "github-sso-session-clock/1.0"

SSO_HEADER = "x-github-sso"

# Inside this window the answer is "arrange it now" rather than "it is fine".
EXPIRING_SOON_DAYS = 7

TOKEN_PREFIXES = (
    ("github_pat_", "fine-grained PAT"),
    ("ghp_", "classic PAT"),
    ("gho_", "OAuth user token"),
    ("ghu_", "App user-to-server token"),
    ("ghs_", "App installation token"),
    ("ghr_", "App refresh token"),
)

# Credentials that hang off a person's identity-provider session, and therefore
# lapse when it does. The installation token is the one that does not, which is
# the entire durable repair.
LAPSES_WITH_A_PERSON = {
    "classic PAT": True,
    "OAuth user token": True,
    "App user-to-server token": True,
    "fine-grained PAT": True,
    "App installation token": False,
    "App refresh token": False,
    "unknown": True,
}


def read_cost(with_admin=True, pages=1):
    """Requests this run will spend against the core quota. Pure.

    Two reads with the credential under investigation, plus one page of
    authorization records per page walked with the owner's credential.
    """
    return 2 + (pages if with_admin else 0)


def token_kind(token):
    """Name the credential from its prefix. Pure; nothing leaves the machine."""
    value = (token or "").strip()
    for prefix, name in TOKEN_PREFIXES:
        if value.startswith(prefix):
            return name
    return "unknown"


def last_eight(token):
    """The eight characters a record is matched on. Pure.

    Returned for an in-memory comparison only. Every caller in this script
    keeps the result out of logs and out of the report.
    """
    value = (token or "").strip()
    return value[-8:] if len(value) >= 8 else ""


def match_authorization(records, tail):
    """Find the record for this credential. Pure.

    Compares token_last_eight and returns the record or None. A missing match
    is a real finding rather than an error: a credential with no authorization
    record has never been authorized for this organization.
    """
    if not tail:
        return None
    for record in records or []:
        if not isinstance(record, dict):
            continue
        if str(record.get("token_last_eight") or "") == tail:
            return record
    return None


def parse_ts(value):
    """ISO 8601 with a Z into an aware datetime, or None. Pure."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def days_left(expires_at, now):
    """Whole days from now until the grant lapses, negative if it has. Pure."""
    when = parse_ts(expires_at)
    if when is None:
        return None
    return int((when - now).total_seconds() // 86400)


def authorization_state(record, now, refused):
    """Classify one credential's SAML standing. Pure. (state, detail).

    The record and the current refusal are read together, because either alone
    gets a case wrong: a missing record with no refusal is simply an
    organization that does not enforce SAML, and an active record beside a
    refusal is a refusal SAML does not explain.
    """
    if record is None:
        if refused:
            return ("never-authorized",
                    "no authorization record exists for this credential, so it "
                    "has never been authorized for this organization. That is a "
                    "first authorization rather than a lapse, and a different "
                    "note owns it.")
        return ("no-record-no-refusal",
                "no authorization record and nothing being refused, which is "
                "what an organization that does not enforce SAML looks like.")
    remaining = days_left(record.get("authorized_credential_expires_at"), now)
    if remaining is None:
        return ("expiry-not-published",
                "the record exists but carries no expiry, so this grant is not "
                "on a clock the API will show you. A refusal here is worth "
                "re-reading the header for.")
    if remaining < 0:
        return ("authorization-lapsed",
                "this authorization expired %d day(s) ago. The credential is "
                "unchanged and valid; the SAML session behind it ran out."
                % abs(remaining))
    if remaining <= EXPIRING_SOON_DAYS:
        return ("authorization-expiring",
                "this authorization lapses in %d day(s). The credential is "
                "fine; the organization's SAML session behind it is what runs "
                "out." % remaining)
    return ("authorization-active",
            "this authorization is good for another %d day(s)." % remaining)


def lapse_evidence(record):
    """Did this credential demonstrably work here. Pure. (bool, detail)."""
    if record is None:
        return (False, "no record, so there is no evidence of past use.")
    used = parse_ts(record.get("credential_accessed_at"))
    if used is None:
        return (False, "the record carries no last-used time, so past success "
                       "is not provable from it.")
    return (True, "the record was last used at %s, which proves this credential "
                  "did work against this organization." % used.isoformat())


def cadence_note(state):
    """What recurrence a reader should expect. Pure."""
    if state in ("authorization-lapsed", "authorization-expiring",
                 "authorization-active"):
        return ("the organization's re-authentication interval is not published "
                "by the API. What is readable is this grant's expiry, and it "
                "will recur.")
    return ("nothing to forecast from this reading.")


def unattended_verdict(kind):
    """Does this credential type depend on a person staying logged in. Pure."""
    if LAPSES_WITH_A_PERSON.get(kind, True):
        return (True, "a %s hangs off a person's identity-provider session, so "
                      "an unattended job holding one fails whenever that person "
                      "stops logging in." % kind)
    return (False, "a %s does not depend on anyone's identity-provider session, "
                   "which is why it is the answer for unattended work." % kind)


def repair(state, org, kind):
    """The sentence a reader has to act on. Pure."""
    depends, _ = unattended_verdict(kind)
    renew = ("a person re-authenticates at https://github.com/orgs/%s/sso "
             "before that date. This script does not and will not do it."
             % org)
    if state == "authorization-lapsed":
        return (renew.replace("before that date", "to restore this credential")
                + (" For anything unattended, move to an App installation "
                   "token, which never lapses with a person's session."
                   if depends else ""))
    if state == "authorization-expiring":
        return (renew + (" For the job that depends on this, move to an App "
                         "installation token, which never lapses with a "
                         "person's session." if depends else ""))
    if state == "never-authorized":
        return ("authorize the credential for the first time, which is the "
                "sibling problem: the refusal is the same and the repair does "
                "not recur on a session clock.")
    if state == "authorization-active":
        return ("nothing today. Note the date and decide whether an unattended "
                "job should be depending on a human session at all.")
    if state == "expiry-not-published":
        return ("read the refusal's x-github-sso header instead; this record "
                "will not tell you when the grant ends.")
    return "nothing on SAML here."


def get(session, url):
    """One GET. Returns the response object."""
    r = session.get(url, timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: a credential is missing, malformed "
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


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("org", help="the organization enforcing SAML")
    ap.add_argument("--pages", type=int, default=1,
                    help="pages of credential authorizations to walk")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (the credential being diagnosed)")
        return 2
    admin = os.environ.get("GITHUB_ADMIN_TOKEN")

    log.info("read cost: %d request(s) against the core hourly quota",
             read_cost(bool(admin), max(1, args.pages)))

    kind = token_kind(token)
    subject = session_for(token)
    me = get(subject, API + "/user")
    account = (me.json() or {}).get("login") if me.status_code == 200 else None
    log.info("credential: %s, account=%s", kind, account or "unreadable")

    listing = get(subject, API + "/orgs/%s/repos?per_page=1" % args.org)
    sso_form = (listing.headers.get(SSO_HEADER) or "").split(";")[0].strip().lower()
    log.info("GET /orgs/%s/repos -> %s, %s: %s", args.org, listing.status_code,
             SSO_HEADER, sso_form or "absent")
    refused = listing.status_code in (403, 404)

    records = []
    if admin:
        owner = session_for(admin)
        url = (API + "/orgs/%s/credential-authorizations?per_page=100"
               % args.org)
        for _page in range(max(1, args.pages)):
            page = get(owner, url)
            if page.status_code != 200:
                log.warning("credential-authorizations returned HTTP %s. That "
                            "endpoint needs admin:org, so this is usually the "
                            "wrong credential rather than a missing record.",
                            page.status_code)
                break
            body = page.json()
            records.extend(body if isinstance(body, list) else [])
            nxt = (page.links or {}).get("next", {}).get("url")
            if not nxt:
                break
            url = nxt
    else:
        log.warning("no GITHUB_ADMIN_TOKEN, so the dated record cannot be read. "
                    "Without it a lapse and a first authorization look "
                    "identical, and the header is all you have.")

    # Compared in memory. These characters are never logged and never appear in
    # the report below.
    record = match_authorization(records, last_eight(token))
    if admin:
        log.info("credential-authorizations: %d record(s) read, %d matched "
                 "(matched on the last eight characters, in memory; they are "
                 "not printed)", len(records), 1 if record else 0)
    if record:
        log.info("credential_type=%s credential_accessed_at=%s",
                 record.get("credential_type"), record.get("credential_accessed_at"))
        log.info("authorized_credential_expires_at=%s",
                 record.get("authorized_credential_expires_at"))

    now = datetime.now(timezone.utc)
    state, detail = authorization_state(record, now, refused)
    proven, proof = lapse_evidence(record)
    log.info("%s: %s", state, detail)
    log.info("past use: %s", proof)
    log.info("cadence: %s", cadence_note(state))
    depends, depends_detail = unattended_verdict(kind)
    log.info("unattended: %s", depends_detail)
    log.info("repair: %s", repair(state, args.org, kind))

    print(json.dumps({
        "organization": args.org,
        "account": account,
        "credential_kind": kind,
        "listing_status": listing.status_code,
        "sso_form": sso_form or None,
        "records_read": len(records),
        "record_matched": bool(record),
        "credential_type": (record or {}).get("credential_type"),
        "credential_accessed_at": (record or {}).get("credential_accessed_at"),
        "authorized_credential_expires_at":
            (record or {}).get("authorized_credential_expires_at"),
        "days_left": days_left((record or {}).get(
            "authorized_credential_expires_at"), now),
        "state": state,
        "detail": detail,
        "past_use_proven": proven,
        "depends_on_a_person": depends,
        "repair": repair(state, args.org, kind),
    }, indent=2, default=str))
    return 1 if state in ("authorization-lapsed", "authorization-expiring") else 0


if __name__ == "__main__":
    sys.exit(main())
