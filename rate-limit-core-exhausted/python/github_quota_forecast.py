"""Forecast when the core REST bucket empties, from three published numbers.

Read only. Every request is a GET, and GET /rate_limit is documented as not
counting against the primary rate limit, so this never spends what it measures.

The forecast is the point. A bucket that is already empty needs no analysis,
only a clock. The question worth asking is whether the drain running right now
fits inside the window that is left, and that is arithmetic over used, limit
and reset.
"""
import argparse
import json
import logging
import os
import sys
import time

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_quota_forecast")

API = "https://api.github.com"
UA = "github-quota-forecast/1.0"

# The core bucket is a fixed one-hour window that refills in full at `reset`,
# which is what makes the elapsed time knowable from a single sample.
WINDOW = 3600.0


def window_burn(used, limit, reset, now, window=WINDOW):
    """Average drain since this window opened, and where it lands. Pure.

    reset is an epoch second and the window is fixed, so the window opened at
    reset - window. That is the whole trick: it converts a counter, which says
    nothing on its own, into a rate. 2,400 used is comfortable at minute fifty
    and an emergency at minute five.
    """
    try:
        used = max(0, int(used))
        limit = max(1, int(limit))
        left = float(reset) - float(now)
    except (TypeError, ValueError):
        return None

    # A reset further away than the window itself means the clocks disagree.
    # Clamping is honest here: it makes elapsed small, which makes the drain
    # look high, which is the safe direction to be wrong in.
    left = min(max(left, 0.0), window)
    elapsed = max(1.0, window - left)
    remaining = max(0, limit - used)

    per_min = used / (elapsed / 60.0)
    left_min = left / 60.0
    projected = used + per_min * left_min
    # What you may still spend per minute and finish the window on zero.
    affordable = remaining / left_min if left_min > 0 else float(remaining)

    if remaining <= 0:
        empty_in = 0.0
    elif per_min <= 0:
        empty_in = None
    else:
        empty_in = remaining / (per_min / 60.0)
        if empty_in > left:
            empty_in = None  # the window refills first

    return {"used": used, "limit": limit, "remaining": remaining,
            "elapsed": round(elapsed, 1), "left": round(left, 1),
            "per_min": round(per_min, 2), "affordable": round(affordable, 2),
            "projected": round(projected), "empty_in": empty_in}


def sample_burn(first, second):
    """Drain between two samples of the same bucket. Pure.

    Returns (state, per_min). The average over the window is history; this is
    the rate right now, and the two disagree exactly when it matters, which is
    when a job burst and stopped or is bursting and has not stopped.

    A window that rolled between the samples resets `used` to nearly zero, so
    the difference goes negative. That is not a negative drain, it is a refill,
    and reporting it as "rolled" beats reporting it as a rate.
    """
    if not first or not second:
        return ("single", None)
    try:
        u1, r1, t1 = int(first["used"]), float(first["reset"]), float(first["at"])
        u2, r2, t2 = int(second["used"]), float(second["reset"]), float(second["at"])
    except (KeyError, TypeError, ValueError):
        return ("single", None)

    gap = t2 - t1
    if gap <= 0:
        return ("no-gap", None)
    if r2 != r1 or u2 < u1:
        return ("rolled", None)
    return ("measured", round((u2 - u1) / (gap / 60.0), 2))


def verdict(win, instant=("single", None), tight=0.8):
    """Turn the arithmetic into one finding. Pure.

    Prefers the measured drain over the window average when there is one,
    because the average is a claim about the past and the measurement is a
    claim about now.
    """
    if not win:
        return ("unreadable", "the rate-limit body did not contain usable numbers")

    state, measured = instant
    drain = measured if (state == "measured" and measured is not None) else win["per_min"]
    source = ("measured over the sample gap" if state == "measured"
              else "averaged over the window so far")
    mins = win["left"] / 60.0

    if win["remaining"] <= 0:
        return ("exhausted",
                "0 of %d left. Every non-search REST call refuses until reset, "
                "in %d second(s). Waiting is not the repair, spending less is."
                % (win["limit"], int(win["left"])))

    if drain > win["affordable"] and drain > 0:
        empty = win["remaining"] / (drain / 60.0)
        return ("will-exhaust",
                "drain is %.1f/min (%s) against %.1f/min affordable. %d left "
                "empties in about %d minute(s), %d minute(s) before reset."
                % (drain, source, win["affordable"], win["remaining"],
                   round(empty / 60.0), max(0, round(mins - empty / 60.0))))

    if (state == "measured" and measured is not None
            and win["per_min"] > 0 and measured > win["per_min"] * 2):
        return ("spiky",
                "drain is %.1f/min right now against a %.1f/min average for the "
                "window. The bucket fits it today, but the average is hiding a "
                "burst and a longer burst will not fit."
                % (measured, win["per_min"]))

    if win["used"] >= win["limit"] * tight:
        return ("tight",
                "%d of %d used with %d minute(s) to reset. The current drain of "
                "%.1f/min fits, but there is no room for a second consumer on "
                "this token." % (win["used"], win["limit"], round(mins), drain))

    return ("clear",
            "drain %.1f/min against %.1f/min affordable, %d left with %d "
            "minute(s) to reset."
            % (drain, win["affordable"], win["remaining"], round(mins)))


def sample(session):
    """One free GET of the whole rate-limit document."""
    r = session.get(API + "/rate_limit", timeout=30)
    if r.status_code != 200:
        log.error("GET /rate_limit returned %d: %s", r.status_code, r.text[:200])
        return None
    body = r.json()
    return {"resources": body.get("resources", {}), "at": time.time()}


def bucket(snapshot, name):
    """Pull one named bucket out of a snapshot as a flat dict."""
    b = (snapshot or {}).get("resources", {}).get(name) or {}
    return {"used": b.get("used", 0), "limit": b.get("limit", 0),
            "reset": b.get("reset", 0), "remaining": b.get("remaining", 0),
            "at": (snapshot or {}).get("at", 0)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--resource", default="core",
                    help="which bucket to forecast (default core)")
    ap.add_argument("--watch", type=int, default=0, metavar="SECONDS",
                    help="take a second sample after this many seconds to "
                         "measure the drain right now (0 = one sample only)")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    first = sample(session)
    if first is None:
        return 2

    for name, b in sorted(first["resources"].items()):
        log.info("bucket %-22s %5s / %-6s remaining %s",
                 name, b.get("used"), b.get("limit"), b.get("remaining"))

    second = None
    if args.watch > 0:
        log.info("second sample in %d second(s)", args.watch)
        time.sleep(args.watch)
        second = sample(session)

    b1 = bucket(first, args.resource)
    b2 = bucket(second, args.resource) if second else None
    win = window_burn(b1["used"], b1["limit"], b1["reset"], first["at"])
    instant = sample_burn(b1, b2)
    state, detail = verdict(win, instant)

    if instant[0] == "rolled":
        log.info("the window rolled between samples: the bucket refilled, so "
                 "there is no drain to measure across that gap")
    log.info("%s: %s", state, detail)

    if state in ("exhausted", "will-exhaust", "tight", "spiky"):
        log.info("repair: send If-None-Match with the etag you already got "
                 "back. A 304 Not Modified does not count against this bucket "
                 "at all, so unchanged data becomes free.")
        log.info("repair: replace per-item REST reads with one GraphQL query. "
                 "GraphQL is billed to a separate bucket, so moving work there "
                 "removes it from this one twice over.")
        log.info("repair: stop polling for changes and subscribe to a webhook, "
                 "so the change arrives instead of being asked for every "
                 "thirty seconds by every consumer of this token.")
        log.info("repair: if the workload is genuinely this large, "
                 "authenticate as a GitHub App installation. That limit scales "
                 "with installed repositories and users, up to 12,500 an hour.")

    print(json.dumps({"resource": args.resource, "state": state,
                      "window": win, "instant": {"state": instant[0],
                                                 "per_min": instant[1]}},
                     indent=2))
    return 1 if state in ("exhausted", "will-exhaust") else 0


if __name__ == "__main__":
    sys.exit(main())
