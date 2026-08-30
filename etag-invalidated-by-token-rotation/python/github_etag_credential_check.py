"""Prove whether a cached ETag survives a change of credential, and cost it.

Read only. Three GETs against one URL, and the third is only issued when a
second credential is available in the environment.

An ETag is scoped to the representation the server produced for that caller, so
rotating a credential invalidates the whole cache at once. For a GitHub App that
happens every hour, on a schedule nobody wrote.
"""
import argparse
import hashlib
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_etag_credential_check")

API = "https://api.github.com"
UA = "github-etag-credential-check/1.0"

# Installation access tokens are valid for one hour.
INSTALLATION_TOKEN_TTL = 3600
HOURLY_LIMIT = 5000


def classify_pair(same, other):
    """Sort the two conditional replays into a finding. Pure.

    `same` is the status when the ETag is replayed with the credential that
    minted it, which is the control. `other` is the status for the same ETag
    under a second credential. Returns (state, detail).
    """
    def code(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    same, other = code(same), code(other)

    if same is None:
        return ("inconclusive", "the control request did not complete, so "
                                "nothing below it can be trusted")
    if same == 200:
        return ("not-cacheable",
                "the endpoint answered 200 to its own etag. Either no validator "
                "came back, something between here and GitHub stripped the "
                "If-None-Match header, or the resource genuinely changed between "
                "the two calls. Rule those out before testing rotation.")
    if same != 304:
        return ("inconclusive",
                "the control request returned %d rather than 304 or 200, which "
                "is not a cache answer at all" % same)

    if other is None:
        return ("unproven",
                "the etag matched its own credential, but no second credential "
                "was available to test the rotation against. The projection "
                "below is arithmetic, not a measurement.")
    if other == 304:
        return ("shared",
                "the same etag matched under both credentials, so rotation is "
                "not what is draining this quota. Look for a poll interval or a "
                "cache key problem instead.")
    if other == 200:
        return ("credential-scoped",
                "the etag that returned 304 for the credential that minted it "
                "returned 200 for another. Every rotation therefore refetches "
                "the entire cache at full price.")
    return ("inconclusive",
            "the second credential returned %d, which is neither a match nor a "
            "miss. Check that it can read this URL at all." % other)


def rotation_waste(urls, poll_interval_s, token_ttl_s,
                   hourly_limit=HOURLY_LIMIT, hours=24):
    """Full responses per day caused by rotation alone. Pure.

    The headline is not the daily total, which is usually modest. It is
    per_rotation: those requests all arrive in the seconds after a mint, which
    is why this reads as a spike rather than as a drift.
    """
    try:
        urls = max(0, int(urls))
    except (TypeError, ValueError):
        urls = 0
    interval = max(1, int(poll_interval_s or 1))
    ttl = max(1, int(token_ttl_s or 1))
    window = max(0, int(hours)) * 3600

    rotations = window // ttl
    polls = (window // interval) * urls
    return {"rotations": rotations,
            "per_rotation": urls,
            "daily": rotations * urls,
            "polls": polls,
            "hourly_share": round(urls / max(1, hourly_limit), 4)}


def token_ttl(expires_at, now):
    """Seconds left on an installation token from its ISO-8601 expires_at. Pure.

    None when it cannot be read, rather than 0: "already expired" and "I could
    not parse this" lead to different next steps.
    """
    if not expires_at:
        return None
    text = str(expires_at).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return max(0, int(parsed.timestamp() - float(now)))
    except (TypeError, ValueError):
        return None


def verdict(state, waste):
    """Combine the measurement and the projection into one finding. Pure."""
    if state in ("not-cacheable", "inconclusive"):
        return (state, "no rotation cost can be projected until the control "
                       "request behaves")
    if state == "shared":
        return ("shared", "rotation is not the problem here")

    share = waste.get("hourly_share", 0)
    per_rotation = waste.get("per_rotation", 0)
    daily = waste.get("daily", 0)

    if state == "unproven" and not daily:
        return ("clear", "nothing to project: no cached urls, or a credential "
                         "that outlives the window")
    if share >= 0.25:
        return ("rotation-dominates",
                "%d full response(s) land in the seconds after every mint, which "
                "is %.0f%% of one hour's entire quota, %d time(s) a day"
                % (per_rotation, share * 100, waste.get("rotations", 0)))
    if daily:
        return ("rotation-costs",
                "%d full response(s) per rotation, %d a day, all of which a "
                "credential-keyed cache would have kept as 304s"
                % (per_rotation, daily))
    return ("clear", "the credential outlives the window, so no rotation cost "
                     "falls inside it")


def fingerprint(token):
    """A stable, non-reversible id for a credential, for use as a cache key.

    The token itself must never be the key: cache keys get logged, dumped and
    put in error messages.
    """
    return hashlib.sha256(("gh:" + str(token)).encode("utf-8")).hexdigest()[:12]


def get(session, url, token, etag=None):
    """One GET, optionally conditional. Returns (status, etag, used)."""
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    }
    if etag:
        headers["If-None-Match"] = etag
    r = session.get(url, headers=headers, timeout=30)
    lowered = {k.lower(): v for k, v in r.headers.items()}
    return r.status_code, lowered.get("etag"), lowered.get("x-ratelimit-used")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default="/user",
                    help="any GET that returns an etag; use one you poll")
    ap.add_argument("--urls", type=int, default=40,
                    help="how many distinct urls your cache holds")
    ap.add_argument("--interval", type=int, default=30,
                    help="seconds between polls of each url")
    ap.add_argument("--ttl", type=int, default=INSTALLATION_TOKEN_TTL,
                    help="credential lifetime in seconds (an installation token "
                         "is 3600)")
    ap.add_argument("--expires-at",
                    help="ISO-8601 expires_at from an installation token, if you "
                         "have one; overrides --ttl for the report")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2
    second = os.environ.get("GITHUB_TOKEN_SECOND")

    url = API + args.path if args.path.startswith("/") else args.path
    session = requests.Session()

    first_status, etag, used_before = get(session, url, token)
    if first_status != 200 or not etag:
        log.error("first GET %s returned %d with etag %r; pick a url that "
                  "returns a validator", url, first_status, etag)
        return 2
    log.info("cache key would be (%s, %s)", fingerprint(token), args.path)

    same_status, _, used_control = get(session, url, token, etag)
    log.info("control: same credential, same etag -> %d", same_status)

    other_status = None
    if second:
        other_status, _, _ = get(session, url, second, etag)
        log.info("rotation: second credential, same etag -> %d", other_status)
    else:
        log.warning("set GITHUB_TOKEN_SECOND to a second credential to measure "
                    "the rotation rather than project it")

    state, detail = classify_pair(same_status, other_status)
    log.info("%s: %s", state, detail)

    ttl = args.ttl
    if args.expires_at:
        left = token_ttl(args.expires_at, time.time())
        if left is None:
            log.warning("could not read --expires-at %r; falling back to --ttl",
                        args.expires_at)
        else:
            log.info("the credential you named expires in %ds", left)

    waste = rotation_waste(args.urls, args.interval, ttl)
    final, why = verdict(state, waste)
    log.info("%s: %s", final, why)

    if final in ("rotation-dominates", "rotation-costs"):
        log.info("repair: key the cache by (credential fingerprint, url) so a "
                 "rotation is an honest miss rather than a silent one.")
        log.info("repair: hold one installation token for its full hour and "
                 "refresh a minute before expires_at, rather than minting a "
                 "fresh one per request.")

    print(json.dumps({"measured": state, "state": final, "waste": waste,
                      "used_before": used_before, "used_control": used_control},
                     indent=2))
    return 1 if final in ("rotation-dominates", "rotation-costs",
                          "not-cacheable") else 0


if __name__ == "__main__":
    sys.exit(main())
