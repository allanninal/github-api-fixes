"""Compare a token's granted lifetime against the interval you rotate on.

Read only. One free call plus two optional cheap ones. Nothing is minted,
rotated or revoked: the repair is a schedule change and a policy only an
organization owner can alter, and both are printed rather than performed.

The point of the note: an organization can cap how long a fine-grained token
may live, and the failure that produces is not an expiry. The policy blocks a
non-compliant token from that organization while it keeps working everywhere
else, so every global check on the credential comes back clean.

What this can and cannot see: the expiry is a header on any authenticated
response. The issue date is not on the wire at all and has to be supplied. The
organization's maximum-lifetime setting has no documented endpoint, so it is a
declared number here and is labelled as one.

Environment:

    GITHUB_TOKEN    the credential whose lifetime is in question
"""
import argparse
import calendar
import json
import logging
import os
import sys
import time

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_token_lifetime")

API = "https://api.github.com"
UA = "github-token-lifetime/1.0"
HEADER = "github-authentication-token-expiration"
DAY = 86400.0

# The documented default ceiling for a fine-grained personal access token. A
# policy can be shorter; nothing readable through the API says what it is.
DEFAULT_FINE_GRAINED_MAX_DAYS = 366

TOKEN_PREFIXES = (
    ("github_pat_", "fine-grained PAT"),
    ("ghp_", "classic PAT"),
    ("gho_", "OAuth user token"),
    ("ghu_", "App user-to-server token"),
    ("ghs_", "App installation token"),
    ("ghr_", "App refresh token"),
)


def read_cost(with_org, with_grants):
    """(requests, quota units) this run will spend. Pure.

    Two numbers because they are not the same budget and the difference is the
    reason the probe is /rate_limit: that call reports the quota rather than
    consuming it, so a lifetime check can run every hour and cost nothing.
    """
    requests_made = 1 + (1 if with_org else 0) + (1 if with_grants else 0)
    return (requests_made, requests_made - 1)


def token_kind(token):
    """Name the credential from its prefix. Pure; nothing leaves the machine."""
    value = (token or "").strip()
    for prefix, name in TOKEN_PREFIXES:
        if value.startswith(prefix):
            return name
    return "unknown"


def policy_applies(kind):
    """Does a maximum-lifetime policy govern this class. Pure. (state, detail)."""
    if kind == "fine-grained PAT":
        return ("policy-applies",
                "the maximum-lifetime policy applies to this class. The "
                "documented default ceiling is %d days and an organization or "
                "enterprise can set something much shorter."
                % DEFAULT_FINE_GRAINED_MAX_DAYS)
    if kind == "classic PAT":
        return ("different-class",
                "classic tokens have no expiry requirement, so a "
                "maximum-lifetime policy does not cover them. An organization "
                "restricts them by blocking classic access altogether, which "
                "is a different refusal, and a classic token that dies after a "
                "long silence is the auto-revocation note.")
    if kind in ("App installation token", "App refresh token"):
        return ("minted-hourly",
                "installation tokens live about an hour and are minted on "
                "demand, so there is no lifetime for a policy to cap and no "
                "rotation for a runbook to schedule.")
    if kind in ("OAuth user token", "App user-to-server token"):
        return ("different-model",
                "this credential's life is governed by its authorization and "
                "refresh flow rather than by a token lifetime policy.")
    return ("class-unknown",
            "the credential class could not be named from its prefix, so "
            "whether the policy applies is unknown.")


def parse_stamp(value):
    """Epoch seconds from a timestamp, or None. Pure. No regular expression.

    The documented header shape is "2026-09-30 12:00:00 UTC"; an ISO instant
    with a Z turns up too. Anything else returns None rather than a plausible
    wrong date, because a wrong lifetime is worse than an unknown one.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    upper = text.upper()
    if upper.endswith(" UTC") or upper.endswith(" GMT"):
        text = text[:-4].strip()
    elif upper.endswith("Z"):
        text = text[:-1].strip()
    text = text.replace("T", " ")
    date_part, _, time_part = text.partition(" ")
    bits = date_part.split("-")
    if len(bits) != 3 or not all(b.isdigit() for b in bits):
        return None
    hour, minute, second = 0, 0, 0
    if time_part.strip():
        clock = time_part.strip().split(":")
        if not all(c.split(".")[0].isdigit() for c in clock):
            return None
        parts = [int(c.split(".")[0]) for c in clock]
        while len(parts) < 3:
            parts.append(0)
        hour, minute, second = parts[0], parts[1], parts[2]
    try:
        return float(calendar.timegm((int(bits[0]), int(bits[1]), int(bits[2]),
                                      hour, minute, second, 0, 0, 0)))
    except (ValueError, OverflowError):
        return None


def header_value(headers, name=HEADER):
    """Case-insensitive header read against a plain dict. Pure."""
    if not isinstance(headers, dict):
        return None
    wanted = str(name).lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return value
    return None


def days_between(earlier, later):
    """Whole-ish days between two epochs, or None. Pure."""
    if earlier is None or later is None:
        return None
    return (later - earlier) / DAY


def granted_lifetime_days(issued_epoch, expires_epoch):
    """The life this token was actually given, or None. Pure.

    None is the common answer and it is an honest one: the issue date is not on
    the wire, so without it a token with 40 days left is indistinguishable from
    a short one at its start and a long one near its end.
    """
    span = days_between(issued_epoch, expires_epoch)
    if span is None or span <= 0:
        return None
    return span


def cap_verdict(granted_days, org_max_days):
    """Is the granted lifetime over the declared cap. Pure. (state, detail)."""
    if org_max_days is None:
        return ("cap-not-declared",
                "no maximum was declared. There is no documented endpoint that "
                "returns an organization's maximum-lifetime setting, so this "
                "number has to come from a person.")
    if granted_days is None:
        return ("lifetime-unknown",
                "the granted lifetime is unknown without an issue date, so it "
                "cannot be compared against the cap.")
    if granted_days > org_max_days:
        return ("over-org-cap",
                "the granted lifetime is %d day(s), longer than the declared "
                "cap of %d. A token over the cap is blocked at that "
                "organization while it keeps working everywhere else: the "
                "policy refuses tokens, it does not shorten them."
                % (round(granted_days), org_max_days))
    return ("within-org-cap",
            "the granted lifetime of %d day(s) is inside the declared cap of "
            "%d." % (round(granted_days), org_max_days))


def rotation_fit(granted_days, remaining_days, rotation_days):
    """Compare two periods, not a date and today. Pure. (state, detail).

    The distinction the note exists for. One of these states is a calendar
    entry; the other is a schedule that cannot work and will fail once per
    cycle until somebody changes it.
    """
    if rotation_days is None:
        return ("rotation-not-declared",
                "no rotation interval was declared, so there is nothing to "
                "compare a lifetime against.")
    if remaining_days is not None and remaining_days < 0:
        return ("already-expired",
                "the expiry is in the past. That is the ordinary expiry note, "
                "not a policy problem.")
    if granted_days is not None and rotation_days > granted_days:
        return ("rotation-outlives-token",
                "you rotate every %d day(s) and this token was granted %d. "
                "That breaks once per cycle, forever, and rotating earlier "
                "this once will not change it."
                % (rotation_days, round(granted_days)))
    if remaining_days is not None and rotation_days > remaining_days:
        return ("this-cycle-expires-first",
                "this token dies in %d day(s) and the next scheduled rotation "
                "is %d away. A one-off: rotate early and the schedule is still "
                "sound." % (round(remaining_days), rotation_days))
    if granted_days is None:
        return ("lifetime-unknown",
                "days remaining are known and the granted lifetime is not, so "
                "whether the schedule works in general cannot be decided from "
                "this reading.")
    return ("fits",
            "the rotation interval is inside the granted lifetime, so the "
            "schedule works on its own terms.")


def expiry_absent_meaning(kind):
    """What a missing expiry header means for this class. Pure. (state, detail)."""
    if kind == "classic PAT":
        return ("no-expiry-on-this-class",
                "a classic token with no expiry emits no header. That is not "
                "reassurance: a credential that never expires is a larger "
                "exposure than one that does, and it has its own note.")
    if kind in ("App installation token", "App refresh token"):
        return ("short-lived-by-design",
                "this class is minted for about an hour, so an absent header "
                "is the expected state and nothing here needs an alarm.")
    return ("expiry-not-reported",
            "no expiry header came back for a class that usually carries one. "
            "Either the response was not authenticated or this credential has "
            "no expiry at all; check which before concluding anything.")


def org_probe_verdict(self_status, org_status):
    """The shape of a policy block, without claiming it. Pure. (state, detail)."""
    mine = int(self_status or 0)
    theirs = None if org_status is None else int(org_status)
    if mine not in (200, 204):
        return ("credential-dead",
                "the credential did not authenticate at all, so nothing here "
                "is about one organization's policy.")
    if theirs is None:
        return ("org-not-probed",
                "no organization was probed, so the reading is about the "
                "credential in general rather than about one namespace.")
    if theirs in (200, 204):
        return ("org-reachable",
                "the organization answered, so nothing is blocking this "
                "credential there right now.")
    if theirs in (401, 403, 404):
        return ("refused-by-one-org",
                "the credential authenticates globally and is refused at this "
                "organization. Three things produce that shape: a token over a "
                "lifetime policy, a fine-grained token still waiting for owner "
                "approval, and a SAML authorization that has lapsed. Each has "
                "its own note; this reading narrows the search rather than "
                "ending it.")
    return ("org-probe-inconclusive",
            "HTTP %s from the organization is not a refusal or a success, so "
            "it says nothing about policy." % org_status)


def grants_over_cap(grants, org_max_days, now_epoch):
    """Which fine-grained tokens reaching the org die when. Pure. list.

    Fed by the App-only organization endpoint. Sorted soonest-first because the
    useful question is which credential goes next, not how many there are.
    """
    out = []
    for grant in grants or []:
        if not isinstance(grant, dict):
            continue
        owner = (grant.get("owner") or {}).get("login")
        expires = parse_stamp(grant.get("token_expires_at"))
        remaining = days_between(now_epoch, expires) if expires else None
        out.append({
            "owner": owner,
            "token_expires_at": grant.get("token_expires_at"),
            "expired": bool(grant.get("token_expired")),
            "days_remaining": None if remaining is None else round(remaining, 1),
            "no_expiry": grant.get("token_expires_at") is None,
            "over_declared_cap": (org_max_days is not None
                                  and grant.get("token_expires_at") is None),
        })
    out.sort(key=lambda row: (row["days_remaining"] is None,
                              row["days_remaining"] if row["days_remaining"] is not None else 0))
    return out


def verdict(cap_state, fit_state, applies_state):
    """The finding, in one state. Pure. (state, detail)."""
    if applies_state in ("different-class", "minted-hourly", "different-model"):
        return (applies_state,
                "a maximum-lifetime policy does not govern this credential "
                "class, so this note is not about your problem.")
    if cap_state == "over-org-cap":
        return ("blocked-by-lifetime-policy",
                "this token is longer-lived than the declared cap, which is "
                "the state that gets refused at that organization while every "
                "global check on the credential passes.")
    if fit_state == "rotation-outlives-token":
        return ("schedule-cannot-work",
                "the rotation interval is longer than any lifetime available "
                "here. This is a process finding, not an incident, and it will "
                "produce an outage every cycle until the schedule changes.")
    if fit_state == "this-cycle-expires-first":
        return ("rotate-early-this-once",
                "this particular token dies before the next scheduled "
                "rotation. Bring the rotation forward; the schedule itself is "
                "sound.")
    if fit_state == "already-expired":
        return ("expired",
                "the expiry has passed, which is the plain expiry case and has "
                "its own note.")
    if "unknown" in cap_state or "unknown" in fit_state:
        return ("lifetime-unknown",
                "not enough was supplied to compare periods. The issue date "
                "and the rotation interval are both facts only you hold.")
    return ("within-policy",
            "the granted lifetime is inside the declared cap and the rotation "
            "interval is inside the lifetime.")


def repair(state, rotation_days, org_max_days):
    """The sentence a reader has to act on. Pure. Nothing here rotates."""
    if state in ("blocked-by-lifetime-policy", "schedule-cannot-work"):
        cap = org_max_days if org_max_days is not None else "the enforced maximum"
        return ("shorten the rotation interval to fit inside %s day(s) and "
                "alert on the expiry header rather than on a calendar. Where "
                "that cadence is impractical, move this job to a GitHub App "
                "whose installation tokens are minted hourly and need no "
                "rotation at all. Nothing here rotates anything."
                % cap)
    if state == "rotate-early-this-once":
        return ("bring this rotation forward: the token dies before the next "
                "scheduled one. The interval of %s day(s) is otherwise fine."
                % rotation_days)
    if state == "expired":
        return ("mint a replacement. This is the plain expiry case and the "
                "policy comparison is not what failed.")
    if state == "lifetime-unknown":
        return ("supply the issue date recorded when this token was minted and "
                "the rotation interval from the runbook, then re-run. Neither "
                "is on the wire.")
    if state in ("different-class", "minted-hourly", "different-model"):
        return ("no action from this note; the credential class is not the one "
                "a lifetime policy governs.")
    return "nothing to repair from this reading."


def get(session, path):
    """One GET. Returns the response object."""
    return session.get(API + path, timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issued", help="YYYY-MM-DD the token was minted")
    parser.add_argument("--rotation-days", type=int,
                        help="how often the runbook says to rotate")
    parser.add_argument("--org-max-days", type=int,
                        help="the maximum lifetime somebody told you the org "
                             "enforces; not readable through the API")
    parser.add_argument("--org", help="an organization to probe")
    parser.add_argument("--org-grants", action="store_true",
                        help="list the org's fine-grained grants; needs a "
                             "GitHub App token with that permission")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (the credential whose lifetime is in question)")
        return 2

    made, spent = read_cost(bool(args.org), bool(args.org_grants))
    log.info("read cost: %d REST request(s), %d of which count against the "
             "core quota", made, spent)

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub refuses requests with no User-Agent before it looks at auth.
        "User-Agent": UA,
    })

    kind = token_kind(token)
    applies_state, applies_detail = policy_applies(kind)
    log.info("credential: %s. %s", kind, applies_detail)

    probe = get(session, "/rate_limit")
    raw_expiry = header_value(dict(probe.headers))
    now = time.time()
    expires_epoch = parse_stamp(raw_expiry)
    remaining = days_between(now, expires_epoch)
    if raw_expiry:
        log.info("expiry header: %s (%s day(s) remaining)", raw_expiry,
                 "unknown" if remaining is None else round(remaining))
    else:
        absent_state, absent_detail = expiry_absent_meaning(kind)
        log.info("%s: %s", absent_state, absent_detail)

    issued_epoch = parse_stamp(args.issued) if args.issued else None
    granted = granted_lifetime_days(issued_epoch, expires_epoch)
    if granted is not None:
        log.info("granted lifetime: %d day(s), from the issue date you supplied",
                 round(granted))

    cap_state, cap_detail = cap_verdict(granted, args.org_max_days)
    log.info("%s: %s", cap_state, cap_detail)

    fit_state, fit_detail = rotation_fit(granted, remaining, args.rotation_days)
    log.info("%s: %s", fit_state, fit_detail)

    org_status = None
    if args.org:
        org_probe = get(session, "/orgs/%s/repos?per_page=1" % args.org)
        org_status = org_probe.status_code
    shape_state, shape_detail = org_probe_verdict(probe.status_code, org_status)
    log.info("org probe: %s — %s", shape_state, shape_detail)

    grants = []
    if args.org_grants and args.org:
        listing = get(session, "/orgs/%s/personal-access-tokens?per_page=100" % args.org)
        if listing.status_code == 200:
            grants = grants_over_cap(listing.json(), args.org_max_days, now)
            log.info("org grants: %d fine-grained token(s) reach %s", len(grants),
                     args.org)
        else:
            log.info("org grants unreadable (HTTP %s). That endpoint is usable "
                     "only by a GitHub App with the organization's personal "
                     "access token permission.", listing.status_code)

    state, detail = verdict(cap_state, fit_state, applies_state)
    log.info("%s: %s", state, detail)
    fix = repair(state, args.rotation_days, args.org_max_days)
    log.info("repair: %s", fix)

    print(json.dumps({
        "token_kind": kind,
        "policy_applies": applies_state,
        "expiry_header": raw_expiry,
        "days_remaining": None if remaining is None else round(remaining, 1),
        "granted_lifetime_days": None if granted is None else round(granted, 1),
        "declared_org_max_days": args.org_max_days,
        "declared_rotation_days": args.rotation_days,
        "cap_state": cap_state,
        "rotation_state": fit_state,
        "org_probe_state": shape_state,
        "org_grants": grants[:20],
        "state": state,
        "detail": detail,
        "repair": fix,
    }, indent=2, default=str))
    return 1 if state in ("blocked-by-lifetime-policy", "schedule-cannot-work",
                          "rotate-early-this-once", "expired") else 0


if __name__ == "__main__":
    sys.exit(main())
