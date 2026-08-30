"""Measure the clock on the machine that signs GitHub App JWTs.

Read only, and the part that matters needs no credential at all. Every GitHub
response carries a Date header, so the reference clock is free: send a request,
note the local time on both sides of it, and the offset between this host and
GitHub falls out with an error bar attached.

GET /rate_limit is used for the samples because it answers unauthenticated and
does not consume quota. Nothing here is written, minted or changed.

This script does not open your JWT. The arithmetic on iat and exp belongs to a
different note; the question here is whether the machine writing iat agrees
with the machine reading it. A host running fast puts iat in GitHub's future
and the JWT is refused however carefully the claim was computed.

Sign convention throughout: skew is local minus server, so a positive number
means this host is ahead of GitHub, which is the direction that breaks a JWT.
"""
import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import timezone
from email.utils import parsedate_to_datetime

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_clock_skew")

API = "https://api.github.com"
UA = "github-clock-skew/1.0"

# The Date header is truncated to the second, so the true server time is
# somewhere inside a one second window. That floor is added to every
# uncertainty rather than pretended away.
DATE_RESOLUTION = 1.0

# Below this the two clocks are close enough that nothing is actionable.
GRACE = 5

# GitHub's own documented advice for the signing code.
RECOMMENDED_BACKDATE = 60

# A drift rate computed over a shorter span than this is noise, given that
# every sample is quantised to a whole second.
MIN_DRIFT_SPAN = 60

# Roughly the discipline a working time daemon holds. Above it the clock is
# free running and will be wrong again whatever you set it to today.
FREE_RUNNING_PPM = 100


def parse_http_date(value):
    """Parse an RFC 9110 Date header into epoch seconds. Pure.

    Returns None rather than raising: a response without a usable Date is a
    sample to discard, not an exception to propagate out of a measurement.
    """
    if not value:
        return None
    try:
        moment = parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def sample_skew(server_epoch, sent, received):
    """One exchange reduced to an offset with an error bar. Pure.

    The request left at `sent` and the response landed at `received`, so the
    server read its clock somewhere in between. Comparing the midpoint of that
    window against the server time gives the offset; half the round trip, plus
    the one second the Date header is quantised to, bounds how wrong it can be.
    """
    if server_epoch is None:
        return None
    round_trip = max(float(received) - float(sent), 0.0)
    midpoint = (float(sent) + float(received)) / 2.0
    return {"skew": round(midpoint - float(server_epoch), 3),
            "uncertainty": round(round_trip / 2.0 + DATE_RESOLUTION, 3),
            "round_trip": round(round_trip, 3),
            "at": round(float(received), 3)}


def best_sample(samples):
    """The exchange with the shortest round trip. Pure.

    Not the mean and not the median. The fastest exchange is the one with the
    least room to be asymmetric between the two directions, which is the same
    reason a time daemon prefers it.
    """
    usable = [s for s in samples if s]
    if not usable:
        return None
    return min(usable, key=lambda s: s["round_trip"])


def timezone_suspect(skew):
    """Hours of offset when the skew looks like a timezone rather than drift. Pure.

    An offset within a minute and a half of a whole or half hour is almost
    never a clock: it is a naive local datetime that was treated as UTC. Saying
    so matters, because backdating sixty seconds does nothing about five hours.
    """
    if skew is None:
        return None
    magnitude = abs(float(skew))
    if magnitude < 1500:
        return None
    slots = round(magnitude / 1800.0)
    if slots == 0:
        return None
    if abs(magnitude - slots * 1800) <= 90:
        hours = slots / 2.0
        return hours if skew > 0 else -hours
    return None


def backdate_needed(skew, uncertainty):
    """How far to backdate iat so this offset cannot reach GitHub's future. Pure."""
    need = float(skew) + float(uncertainty) + GRACE
    if need <= RECOMMENDED_BACKDATE:
        return RECOMMENDED_BACKDATE
    return int(math.ceil(need / 30.0)) * 30


def classify(skew, uncertainty, backdate):
    """Turn one measured offset into a finding. Pure.

    Direction first, because it decides which failure you have. A host ahead of
    GitHub breaks iat; a host behind it burns the JWT lifetime early, which is
    a quieter problem with a different repair.
    """
    if skew is None:
        return ("unmeasurable",
                "no response carried a usable Date header, so there is no "
                "reference clock to compare against. Check that something is "
                "not stripping response headers in front of this host.")

    hours = timezone_suspect(skew)
    if hours is not None:
        return ("timezone-not-drift",
                "the offset is %+.1f hours, which is a timezone conversion "
                "rather than a clock fault. Something built the timestamp from "
                "a naive local datetime and treated it as UTC. Backdating will "
                "not help; the conversion has to be fixed." % hours)

    if skew > 0:
        margin = float(backdate) - (float(skew) + float(uncertainty))
        if margin < 0:
            return ("iat-lands-in-the-future",
                    "this host is %.1fs ahead of GitHub and iat is backdated "
                    "by %ds, so the claim lands %.1fs into GitHub's future and "
                    "the JWT is refused. Backdate by %ds and fix the host clock."
                    % (skew, backdate, -margin, backdate_needed(skew, uncertainty)))
        if margin < GRACE:
            return ("backdate-has-no-headroom",
                    "this host is %.1fs ahead of GitHub and the %ds backdate "
                    "absorbs it with only %.1fs to spare, which is close enough "
                    "to fail on a fast network. Backdate by %ds."
                    % (skew, backdate, margin, backdate_needed(skew, uncertainty)))
        if abs(skew) <= max(uncertainty, GRACE):
            return ("clock-in-sync",
                    "this host and GitHub agree to within the measurement "
                    "error of %.1fs." % uncertainty)
        return ("drift-absorbed-by-backdate",
                "this host is %.1fs ahead of GitHub, and the %ds backdate "
                "covers it with %.1fs to spare. The JWT is safe; the clock is "
                "still wrong and worth fixing." % (skew, backdate, margin))

    if abs(skew) <= max(uncertainty, GRACE):
        return ("clock-in-sync",
                "this host and GitHub agree to within the measurement error "
                "of %.1fs." % uncertainty)
    return ("clock-behind-github",
            "this host is %.1fs behind GitHub. iat is safe, but every JWT "
            "arrives having already spent %.1fs of its life, so a short "
            "lifetime can expire on the way." % (-skew, -skew))


def drift_rate(readings, min_span=MIN_DRIFT_SPAN):
    """Parts per million of drift between the first and last reading. Pure.

    readings: [(local_time, skew), ...]. Returns None when the samples are too
    close together to support a rate, which they usually are: every sample is
    quantised to a whole second, so a few seconds of span can only produce a
    number that looks authoritative and is not.
    """
    usable = [r for r in readings if r and r[1] is not None]
    if len(usable) < 2:
        return None
    span = float(usable[-1][0]) - float(usable[0][0])
    if span < min_span:
        return None
    return round((float(usable[-1][1]) - float(usable[0][1])) / span * 1e6, 1)


def classify_rate(ppm):
    """Say whether the offset is standing still or growing. Pure."""
    if ppm is None:
        return ("rate-not-measurable",
                "the samples do not span %ds, which is the least this "
                "measurement can support. Re-run with a longer interval if you "
                "want a rate rather than an offset." % MIN_DRIFT_SPAN)
    if abs(ppm) <= FREE_RUNNING_PPM:
        return ("offset-is-static",
                "the offset is holding at %.1f ppm, so the clock is "
                "disciplined and was simply set wrong once." % ppm)
    return ("clock-is-running-free",
            "the offset is moving at %.1f ppm, which is about %.1f seconds a "
            "day. Nothing is disciplining this clock, so setting it by hand "
            "buys only a few days." % (ppm, ppm * 0.0864))


def interpret(status, message):
    """Map a confirming GET /app response to the defect it names. Pure.

    Only the iat family is this note's business. The other messages are named
    so the report can point at the right neighbour rather than absorbing them.
    """
    if status == 200:
        return ("accepted", "GitHub did not complain about iat.")
    text = str(message or "").lower()
    if "issued at" in text or "'iat'" in text:
        return ("github-refused-iat",
                "GitHub says iat is not a time that has happened, which is "
                "this host being ahead of it.")
    if "too far in the future" in text:
        return ("lifetime-not-drift",
                "GitHub is complaining about exp rather than iat, so the "
                "requested lifetime is over the ceiling and the clock is not "
                "the problem.")
    if "could not be decoded" in text:
        return ("key-or-encoding",
                "GitHub could not decode the JWT at all, which is a signing "
                "key or encoding fault rather than a clock one.")
    if "integration not found" in text:
        return ("issuer-does-not-resolve",
                "the iss claim does not name an App GitHub can find, which is "
                "a key and issuer problem rather than a clock one.")
    return ("unrelated",
            "the response does not mention a claim, so this failure has "
            "another cause.")


def take_samples(count, interval):
    """Time `count` exchanges against GitHub. The only network in this script."""
    out = []
    for i in range(count):
        if i:
            time.sleep(interval)
        sent = time.time()
        try:
            r = requests.get(API + "/rate_limit", timeout=30, headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": UA,
            })
        except requests.RequestException as err:
            log.warning("sample %d failed: %s", i + 1, err)
            continue
        received = time.time()
        served = parse_http_date(r.headers.get("Date"))
        if served is None:
            log.warning("sample %d carried no usable Date header", i + 1)
            continue
        out.append(sample_skew(served, sent, received))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=int, default=3,
                    help="how many exchanges to time (default 3)")
    ap.add_argument("--interval", type=float, default=2.0,
                    help="seconds between samples; use 30 or more if you want "
                         "a drift rate as well as an offset (default 2)")
    ap.add_argument("--backdate", type=int, default=0,
                    help="the seconds your signing code already subtracts from "
                         "iat (default 0, which is the common case)")
    ap.add_argument("--confirm", action="store_true",
                    help="also send GITHUB_APP_JWT to GET /app and report what "
                         "GitHub says about the claim")
    args = ap.parse_args()

    samples = take_samples(max(args.samples, 1), max(args.interval, 0.0))
    best = best_sample(samples)
    if best is None:
        log.error("no sample produced a reading; nothing can be said about "
                  "this clock")
        return 2

    log.info("best of %d sample(s): skew=%+.1fs uncertainty=%.1fs "
             "round_trip=%.2fs", len(samples), best["skew"],
             best["uncertainty"], best["round_trip"])

    state, detail = classify(best["skew"], best["uncertainty"], args.backdate)
    log.info("%s: %s", state, detail)

    readings = [(s["at"], s["skew"]) for s in samples if s]
    ppm = drift_rate(readings)
    rate_state, rate_detail = classify_rate(ppm)
    log.info("%s: %s", rate_state, rate_detail)

    if args.confirm:
        jwt = os.environ.get("GITHUB_APP_JWT")
        if not jwt:
            log.warning("--confirm needs GITHUB_APP_JWT set to the JWT your "
                        "own signing code produces")
        else:
            # The JWT is sent and nothing else. It is not decoded, stored or
            # logged, in whole or in part.
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

    if state in ("iat-lands-in-the-future", "backdate-has-no-headroom"):
        log.info("repair: set iat to now minus %ds when minting, then "
                 "install time sync on this host so the offset stops moving",
                 backdate_needed(best["skew"], best["uncertainty"]))

    print(json.dumps({"skew_seconds": best["skew"],
                      "uncertainty_seconds": best["uncertainty"],
                      "round_trip_seconds": best["round_trip"],
                      "samples": len(samples), "backdate_seconds": args.backdate,
                      "drift_ppm": ppm, "state": state}, indent=2))
    return 0 if state in ("clock-in-sync", "drift-absorbed-by-backdate") else 1


if __name__ == "__main__":
    sys.exit(main())
