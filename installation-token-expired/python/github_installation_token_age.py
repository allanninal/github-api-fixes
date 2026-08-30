"""Say how much of its hour a GitHub App installation token has left.

Read only. One request, GET /installation/repositories, which is the route an
installation access token can answer and almost nothing else can. Nothing is
minted, refreshed or changed.

Minting is a write - it is a POST to the App installation endpoint - so this
script never does it. The mint moment comes from your own record of it, or the
expiry comes from the header GitHub attaches to a response for a credential
that has one. The report says which source it used rather than implying it
learned the number from a fresh mint.

The token is read from the environment and never printed, in whole or in part.
What the report contains is seconds.
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_installation_token_age")

API = "https://api.github.com"
UA = "github-installation-token-age/1.0"

# Fixed, from the moment of minting, and not extended by use.
LIFETIME = 3600

# Re-mint with this much of the hour still unspent, so a slow mint, a retry and
# an overrunning batch all fit inside the margin.
SAFE_MARGIN = 600
RECOMMENDED_INTERVAL = LIFETIME - SAFE_MARGIN

# Under this, a long batch will cross the line while it is still working.
DANGER_BAND = 300

# Two records of the same token should agree to about this. More than this and
# they are records of different tokens.
RECONCILE_TOLERANCE = 60


def parse_moment(value):
    """Parse an epoch or an ISO-8601 timestamp into epoch seconds. Pure.

    Accepts what a program is likely to have written down: the integer it got
    from time(), or the string GitHub uses in expires_at. Returns None rather
    than raising, because an unparseable record is a finding of its own.
    """
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{9,11}", text):
        return float(text)
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def parse_expiry_header(value):
    """Parse the expiry GitHub puts on a response. Pure.

    It is not ISO-8601: the format is a space-separated date and time followed
    by a zone name, so it needs its own small normalisation before the general
    parser can take it.
    """
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith(" UTC"):
        text = text[:-4].strip().replace(" ", "T") + "+00:00"
    return parse_moment(text)


def remaining(minted_at, expires_at, now):
    """Seconds of life left in the token, and where that number came from. Pure.

    GitHub's own expiry wins when both are available, because your record is a
    record of a mint and GitHub's is a statement about the credential in your
    hand. They should agree; when they do not, that is the finding.
    """
    if expires_at is not None:
        return int(expires_at - now), "github"
    if minted_at is not None:
        return int(minted_at + LIFETIME - now), "record"
    return None, "nothing"


def classify(left):
    """Turn a remaining life into a band. Pure."""
    if left is None:
        return ("no-record",
                "there is no mint time recorded and no expiry on the response, "
                "so nothing can be said about how much of the hour is left. "
                "Record the moment you mint, next to the token.")
    if left <= 0:
        return ("expired",
                "this token ran out %ds ago. Every call made with it returns "
                "401 Bad credentials, all at once, which is why a restart "
                "appears to fix it." % -left)
    if left < DANGER_BAND:
        return ("inside-the-danger-band",
                "%ds remain of the %ds lifetime. A batch that runs longer than "
                "that will cross the line while it is still working."
                % (left, LIFETIME))
    if left < SAFE_MARGIN:
        return ("past-the-safe-margin",
                "%ds remain, which is inside the %ds margin a refresh needs to "
                "cover a slow mint and a retry." % (left, SAFE_MARGIN))
    return ("fresh", "%ds remain of the %ds lifetime." % (left, LIFETIME))


def refresh_verdict(interval):
    """Judge a refresh schedule against the fixed lifetime. Pure.

    This is the half of the check that finds the bug on a healthy process,
    before it has ever fired.
    """
    if not interval or interval <= 0:
        return ("minted-once-at-startup",
                "no refresh interval, so this process mints once and holds. "
                "The first 401 arrives %d minutes after start, on everything "
                "at once." % (LIFETIME // 60))
    if interval >= LIFETIME:
        return ("refresh-slower-than-lifetime",
                "re-minting every %ds against a %ds lifetime is not a refresh, "
                "it is a race. Some days the token is replaced first and some "
                "days it is not." % (interval, LIFETIME))
    if interval > LIFETIME - SAFE_MARGIN:
        return ("refresh-without-margin",
                "re-minting every %ds leaves only %ds of margin, which one "
                "slow mint or one retry uses up." % (interval, LIFETIME - interval))
    return ("refresh-healthy",
            "re-minting every %ds leaves %ds of margin."
            % (interval, LIFETIME - interval))


def cliff_at(minted_at):
    """The epoch second at which 401s begin, or None. Pure."""
    if minted_at is None:
        return None
    return int(minted_at) + LIFETIME


def reconcile(header_expiry, record_expiry):
    """Compare GitHub's expiry against your own record of the mint. Pure.

    A disagreement is not a rounding problem. It means the process is holding a
    different token from the one it wrote down, which is a caching or sharing
    bug wearing an expiry costume.
    """
    if header_expiry is None:
        return ("no-header",
                "the response carried no expiry, so GitHub's view is "
                "unavailable and only your record is in play.")
    if record_expiry is None:
        return ("header-only",
                "there is no recorded mint time to check GitHub's expiry "
                "against. Record one; it costs nothing and it is the only way "
                "to notice a stale token.")
    gap = int(abs(header_expiry - record_expiry))
    if gap <= RECONCILE_TOLERANCE:
        return ("record-agrees",
                "GitHub's expiry and your recorded mint time are %ds apart." % gap)
    return ("record-disagrees",
            "GitHub's expiry and your recorded mint time are %ds apart, so "
            "this process is not holding the token it recorded. Look for a "
            "cached token or two workers sharing one variable." % gap)


def interpret(status, message, left):
    """Map the live response to a cause, using the remaining life. Pure.

    401 Bad credentials is the same sentence for an expired token, a revoked
    one and a truncated one, so the burn-down is what separates them. With no
    record to lean on, the honest answer is that they cannot be separated.
    """
    if status == 200:
        return ("token-live",
                "the token answered the installation route, so it is valid "
                "right now.")
    text = str(message or "").lower()
    if status == 401 and "bad credentials" in text:
        if left is not None and left <= 0:
            return ("expired-as-predicted",
                    "the token is past its hour and GitHub refused it, which "
                    "is exactly the arithmetic above.")
        if left is not None and left > DANGER_BAND:
            return ("not-an-expiry-problem",
                    "%ds of the lifetime remain and GitHub still refused the "
                    "token, so it was revoked, truncated or never valid. That "
                    "is a different investigation." % left)
        return ("expired-or-revoked-cannot-tell",
                "GitHub refused the token and there is no reliable record of "
                "when it was minted, so expiry and revocation look identical "
                "from here.")
    if status == 403 and "not accessible by integration" in text:
        return ("wrong-credential-class",
                "this route accepted the credential and refused the action, "
                "which means what is being held is not an installation access "
                "token at all.")
    if status == 404:
        return ("route-not-answered",
                "a 404 on the installation route usually means the credential "
                "is not an installation access token.")
    return ("unrelated",
            "the response does not look like an expiry, so this failure has "
            "another cause.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh-interval", type=int, default=0,
                    help="the seconds between re-mints in your code; 0 means "
                         "the token is minted once at startup")
    ap.add_argument("--minted-at", default=None,
                    help="when this token was minted, as an epoch second or an "
                         "ISO-8601 timestamp. Defaults to GITHUB_TOKEN_MINTED_AT")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_INSTALLATION_TOKEN")
    if not token:
        log.error("set GITHUB_INSTALLATION_TOKEN to the installation access "
                  "token the process is holding")
        return 2

    minted_at = parse_moment(args.minted_at
                             or os.environ.get("GITHUB_TOKEN_MINTED_AT"))
    now = time.time()

    r = requests.get(API + "/installation/repositories", timeout=30,
                     params={"per_page": 1}, headers={
                         "Authorization": "Bearer " + token,
                         "Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28",
                         "User-Agent": UA,
                     })
    try:
        body = r.json()
    except ValueError:
        body = None
    message = body.get("message") if isinstance(body, dict) else None
    header_expiry = parse_expiry_header(
        r.headers.get("github-authentication-token-expiration"))
    log.info("GET /installation/repositories returned %d", r.status_code)

    left, source = remaining(minted_at, header_expiry, now)
    if minted_at is not None:
        log.info("minted %ds ago", int(now - minted_at))
    if left is not None:
        log.info("%ds left, according to the %s", left, source)

    state, detail = classify(left)
    log.info("%s: %s", state, detail)

    plan_state, plan_detail = refresh_verdict(args.refresh_interval)
    log.info("%s: %s", plan_state, plan_detail)

    record_expiry = None if minted_at is None else minted_at + LIFETIME
    match_state, match_detail = reconcile(header_expiry, record_expiry)
    log.info("%s: %s", match_state, match_detail)

    live_state, live_detail = interpret(r.status_code, message, left)
    log.info("%s: %s", live_state, live_detail)

    if plan_state != "refresh-healthy" or state in ("expired",
                                                    "inside-the-danger-band"):
        log.info("repair: re-mint every %ds, and re-mint again on any 401. A "
                 "timer alone still fails on the day something stalls.",
                 RECOMMENDED_INTERVAL)

    print(json.dumps({"seconds_left": left, "source": source,
                      "cliff_at": cliff_at(minted_at),
                      "refresh_interval": args.refresh_interval,
                      "state": state, "refresh_state": plan_state,
                      "reconcile_state": match_state,
                      "live_state": live_state}, indent=2))
    return 0 if state == "fresh" and plan_state == "refresh-healthy" else 1


if __name__ == "__main__":
    sys.exit(main())
