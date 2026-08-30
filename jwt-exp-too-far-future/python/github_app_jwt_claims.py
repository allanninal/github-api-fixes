"""Audit the iat and exp claims of a GitHub App JWT before GitHub refuses them.

Read only, and mostly offline. The JWT is read from the environment, decoded
locally, and never printed: the report contains three claim values and a
number of seconds, which is everything needed to name the defect and nothing
that could be replayed.

A GitHub App JWT may live at most ten minutes. Both numbers that decide this
are chosen by your own signing code, and reading them back needs no key at all
- verification needs a key, decoding does not. The one request here is
GET /app, which confirms the local verdict. The script stops there on purpose:
exchanging a JWT for an installation access token is a write, and nothing in
this section writes.
"""
import argparse
import base64
import json
import logging
import os
import sys
import time

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_app_jwt_claims")

API = "https://api.github.com"
UA = "github-app-jwt-claims/1.0"

# The server-enforced maximum lifetime, and the values worth using instead.
# 540 leaves a minute of headroom under the ceiling; backdating iat by 60
# absorbs modest drift on the signing machine.
CEILING = 600
RECOMMENDED_LIFETIME = 540
RECOMMENDED_BACKDATE = 60

# How far ahead of the local clock iat may sit before it is worth reporting as
# drift rather than as noise.
SKEW_GRACE = 30


def decode_segment(segment):
    """Base64url-decode one JWT segment into a dict. Pure.

    Returns None rather than raising: a malformed JWT is a finding to report,
    not an exception to propagate out of a diagnostic script.
    """
    text = str(segment or "")
    padded = text + "=" * (-len(text) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        value = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def claims(jwt):
    """Split a JWT and decode its header and payload. Pure.

    The signature segment is counted, to check the shape, and then discarded
    without being decoded, returned or logged. Nothing downstream needs it and
    a diagnostic tool has no business handling it.
    """
    parts = str(jwt or "").strip().split(".")
    if len(parts) != 3:
        return None, None
    return decode_segment(parts[0]), decode_segment(parts[1])


def lifetime(payload):
    """The requested lifetime in seconds, or None if it cannot be computed. Pure."""
    if not isinstance(payload, dict):
        return None
    iat, exp = payload.get("iat"), payload.get("exp")
    if not isinstance(iat, (int, float)) or not isinstance(exp, (int, float)):
        return None
    if isinstance(iat, bool) or isinstance(exp, bool):
        return None
    return int(exp) - int(iat)


def skew(payload, now):
    """How far iat sits from the local clock, in seconds. Pure.

    Negative means the JWT was backdated, which is what you want. Positive
    means the signing clock is ahead of this one.
    """
    if not isinstance(payload, dict):
        return None
    iat = payload.get("iat")
    if not isinstance(iat, (int, float)) or isinstance(iat, bool):
        return None
    return int(iat) - int(now)


def audit(payload, now):
    """Turn a decoded payload and a clock reading into a finding. Pure.

    Order matters. The ceiling is checked before anything clock-relative,
    because a lifetime over 600 seconds is wrong whatever time it is, while
    "expired" and "issued in the future" both depend on a local clock that may
    itself be the defect. Testing the claim-relative fault first stops the
    script blaming drift for a payload that is wrong regardless.
    """
    if not isinstance(payload, dict):
        return ("unreadable",
                "the middle segment did not decode to a JSON object, so this "
                "is not a well-formed JWT. Check what the signing code "
                "returned before looking at any claim.")
    if "iat" not in payload:
        return ("no-iat",
                "there is no iat claim. GitHub measures the lifetime from it, "
                "so a JWT without one cannot be judged against the ten minute "
                "ceiling and is refused.")
    if "exp" not in payload:
        return ("no-exp",
                "there is no exp claim, so the JWT never expires as far as the "
                "payload is concerned. That is exactly what the ceiling exists "
                "to prevent, and it is refused.")

    span = lifetime(payload)
    if span is None:
        return ("non-numeric-claim",
                "iat and exp must be numeric seconds since the epoch. One of "
                "them is not a number, which usually means a date string or a "
                "millisecond timestamp went in where seconds were expected.")
    if span <= 0:
        return ("exp-not-after-iat",
                "exp is %d second(s) before iat, so the JWT is expired at the "
                "moment it is signed." % -span)
    if span > CEILING:
        return ("exp-too-far-future",
                "the requested lifetime is %ds, which is %ds over the %ds "
                "ceiling. Remove %ds from exp and the claim is legal."
                % (span, span - CEILING, CEILING, span - RECOMMENDED_LIFETIME))

    drift = skew(payload, now)
    exp = int(payload["exp"])
    if exp <= int(now):
        return ("already-expired",
                "the lifetime is legal at %ds, and this JWT expired %ds ago. "
                "A JWT minted once and cached for the life of a process fails "
                "exactly like this, minutes after a deploy that looked fine."
                % (span, int(now) - exp))
    if drift is not None and drift > SKEW_GRACE:
        return ("iat-in-the-future",
                "the lifetime is legal at %ds, and iat is %ds ahead of this "
                "clock. If the signing machine is ahead of GitHub, iat lands "
                "in its future and the message names iat rather than exp. "
                "That is a different repair: backdate iat and fix the clock."
                % (span, drift))
    if exp - int(now) < 30:
        return ("expiring-imminently",
                "the lifetime is legal at %ds and only %ds of it remain, which "
                "is not enough to survive a retry. Mint per exchange rather "
                "than caching." % (span, exp - int(now)))
    return ("within-ceiling",
            "the requested lifetime of %ds is inside the %ds ceiling."
            % (span, CEILING))


def recommend(payload, now):
    """The claim values that would have worked. Pure."""
    iat = int(now) - RECOMMENDED_BACKDATE
    span = lifetime(payload)
    return {"iat": iat, "exp": iat + RECOMMENDED_LIFETIME,
            "lifetime": RECOMMENDED_LIFETIME,
            "seconds_to_remove": max((span or 0) - RECOMMENDED_LIFETIME, 0)}


def interpret(status, message):
    """Map a live GET /app response to the defect it names. Pure.

    GitHub's messages about these claims are specific and stable, and each one
    points at a different line of the signing code. Matched on the distinctive
    phrase rather than on the whole sentence.
    """
    if status == 200:
        return ("accepted",
                "the JWT was accepted, so exp and iat are not the problem.")
    text = str(message or "").lower()
    if "too far in the future" in text:
        return ("exp-too-far-future",
                "GitHub says exp is too far ahead of iat, which is the ceiling.")
    if "issued at" in text or "'iat'" in text:
        return ("iat-in-the-future",
                "GitHub says iat is in its future, which is clock drift on the "
                "signing machine rather than a lifetime problem.")
    if "numeric value representing the future" in text or "expired" in text:
        return ("already-expired",
                "GitHub says exp is not in the future, so this JWT was minted "
                "too long ago or the clock is behind.")
    if "could not be decoded" in text:
        return ("undecodable",
                "GitHub could not decode the JWT at all, which is a signing or "
                "encoding fault rather than a claim one.")
    if "integration not found" in text:
        return ("wrong-app-or-key",
                "the claims are acceptable and the App they name cannot be "
                "found, so iss or the signing key belongs to something else.")
    return ("unrelated",
            "the response does not mention a claim, so this failure has "
            "another cause.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true",
                    help="skip the confirming GET /app and report the local "
                         "arithmetic only")
    args = ap.parse_args()

    jwt = os.environ.get("GITHUB_APP_JWT")
    if not jwt:
        log.error("set GITHUB_APP_JWT to the JWT your own signing code "
                  "produces. A JWT minted by this script would prove nothing "
                  "about yours")
        return 2

    now = time.time()
    header, payload = claims(jwt)
    if payload is None:
        log.error("the JWT did not decode into three segments with a JSON "
                  "payload in the middle")
        state, detail = audit(None, now)
        log.info("%s: %s", state, detail)
        return 1

    # Claim values only. The signature is never decoded and the JWT is never
    # printed, in whole or in part.
    log.info("iss=%s iat=%s exp=%s lifetime=%ss skew=%ss",
             payload.get("iss", "absent"), payload.get("iat", "absent"),
             payload.get("exp", "absent"),
             lifetime(payload) if lifetime(payload) is not None else "unknown",
             skew(payload, now) if skew(payload, now) is not None else "unknown")
    if isinstance(header, dict) and header.get("alg") not in (None, "RS256"):
        log.info("note: alg is %s rather than RS256, which is a different "
                 "defect from this one", header.get("alg"))

    state, detail = audit(payload, now)
    log.info("%s: %s", state, detail)

    if not args.offline:
        r = requests.get(API + "/app", timeout=30, headers={
            "Authorization": "Bearer " + jwt,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": UA,
        })
        try:
            body = r.json()
        except ValueError:
            body = None
        message = body.get("message") if isinstance(body, dict) else None
        log.info("GET /app returned %d", r.status_code)
        live_state, live_detail = interpret(r.status_code, message)
        log.info("%s: %s", live_state, live_detail)

    if state in ("exp-too-far-future", "no-exp", "no-iat", "exp-not-after-iat",
                 "non-numeric-claim", "already-expired", "expiring-imminently"):
        want = recommend(payload, now)
        log.info("repair: set iat=%d (now minus %ds) and exp=%d (iat plus "
                 "%ds), then mint a fresh JWT per token exchange rather than "
                 "caching one", want["iat"], RECOMMENDED_BACKDATE, want["exp"],
                 want["lifetime"])
        if want["seconds_to_remove"]:
            log.info("repair: that is %d second(s) off the current exp",
                     want["seconds_to_remove"])

    print(json.dumps({"iss": payload.get("iss"), "iat": payload.get("iat"),
                      "exp": payload.get("exp"), "lifetime": lifetime(payload),
                      "skew_seconds": skew(payload, now), "state": state},
                     indent=2))
    return 0 if state == "within-ceiling" else 1


if __name__ == "__main__":
    sys.exit(main())
