"""Read how long each GitHub credential has left, before it costs you an outage.

Read only. One GET /rate_limit per credential, which is authenticated and
consumes no quota from any bucket, so this is safe to run on a schedule.

GitHub attaches github-authentication-token-expiration to authenticated REST
responses for credentials that carry an expiry. That header is the only place
the date is readable, and it is readable only while the credential still works.
"""
import argparse
import calendar
import json
import logging
import os
import re
import sys
import time

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_token_expiry_watch")

API = "https://api.github.com"
UA = "github-token-expiry-watch/1.0"

HEADER = "github-authentication-token-expiration"
DAY = 86400

# Under two hours on a working credential is a minted GitHub App installation
# token, which is the desired end state rather than an emergency. It is also a
# personal access token in its final two hours, and the header cannot tell them
# apart, so the report says both.
SHORT_LIVED_S = 2 * 3600

# Notice, warning, critical. One alarm at zero is not monitoring.
DEFAULT_THRESHOLDS = (30, 14, 3)

_ISO_T = re.compile(r"^(\d{4}-\d{2}-\d{2})T")
_STAMP = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ ](\d{2}):(\d{2})(?::(\d{2}))?)?$")
_OFFSET = re.compile(r"([+-]\d{2}:?\d{2})$")


def parse_expiry(value):
    """Epoch seconds from the expiry header, or None. Pure.

    The documented shape is "2026-09-30 12:00:00 UTC", but an ISO timestamp with
    a Z or a numeric offset turns up too. Anything that does not parse returns
    None rather than a plausible wrong date, because a wrong expiry is worse
    than no expiry.
    """
    if not isinstance(value, str):
        return None
    # Only the ISO separator, never any other T: the documented shape ends in
    # "UTC", and a blanket replace turns that into "U C".
    text = _ISO_T.sub(r"\1 ", value.strip())
    if not text:
        return None

    offset = "+0000"
    upper = text.upper()
    if upper.endswith(" UTC") or upper.endswith(" GMT"):
        text = text[:-4].strip()
    elif upper.endswith("Z"):
        text = text[:-1].strip()
    else:
        found = _OFFSET.search(text)
        if found:
            offset = found.group(1).replace(":", "")
            text = text[:found.start()].strip()

    stamp = _STAMP.match(text)
    if not stamp:
        return None
    year, month, day, hour, minute, second = stamp.groups()
    sign = 1 if offset[0] == "-" else -1
    shift = sign * (int(offset[1:3]) * 3600 + int(offset[3:5]) * 60)
    base = calendar.timegm((int(year), int(month), int(day), int(hour or 0),
                            int(minute or 0), int(second or 0), 0, 0, 0))
    return base + shift


def header_value(headers, name=HEADER):
    """Case-insensitive header lookup. Pure."""
    for key, value in (headers or {}).items():
        if str(key).lower() == name:
            return value
    return None


def seconds_left(expiry, now):
    """Seconds between now and the expiry; None when either is unreadable. Pure."""
    try:
        return int(expiry) - int(now)
    except (TypeError, ValueError):
        return None


def bucket(remaining, thresholds=DEFAULT_THRESHOLDS):
    """Name the urgency of a remaining lifetime. Pure."""
    if remaining is None:
        return "unknown"
    if remaining <= 0:
        return "expired"
    if remaining < SHORT_LIVED_S:
        return "short-lived"
    notice, warning, critical = thresholds
    days = remaining / DAY
    if days <= critical:
        return "critical"
    if days <= warning:
        return "warning"
    if days <= notice:
        return "notice"
    return "ok"


def reading(name, status, headers, now, thresholds=DEFAULT_THRESHOLDS):
    """One credential's expiry reading, including why there might not be one. Pure.

    The states that matter are the two silences. A request that succeeded and
    carried no expiry header is a finding about the credential. A request that
    failed is a finding about nothing at all, and printing them the same way is
    how a monitoring script gives a permanent token a clean bill of health.
    """
    try:
        status = int(status)
    except (TypeError, ValueError):
        status = 0

    if status == 401:
        return {"name": name, "state": "rejected", "seconds_left": None,
                "why": "the credential was refused, so its expiry is no longer "
                       "a forecast"}
    if not 200 <= status < 300:
        return {"name": name, "state": "unreadable", "seconds_left": None,
                "why": "the probe returned %d, so nothing can be read from its "
                       "headers" % status}

    raw = header_value(headers)
    if raw is None:
        return {"name": name, "state": "no-expiry-reported", "seconds_left": None,
                "why": "the request succeeded and carried no expiry header, "
                       "which means either the credential never expires or its "
                       "class does not report one. The header cannot tell those "
                       "apart"}

    expiry = parse_expiry(raw)
    if expiry is None:
        return {"name": name, "state": "unreadable-header", "seconds_left": None,
                "why": "the expiry header was present but did not parse: %r" % raw}

    remaining = seconds_left(expiry, now)
    return {"name": name, "state": bucket(remaining, thresholds),
            "seconds_left": remaining, "expires_at": expiry,
            "why": "read from the %s response header" % HEADER}


# Urgency first. An unreadable credential outranks one with ninety days left,
# because you have learned nothing about it and that is worse than good news.
ORDER = {"expired": 0, "critical": 1, "warning": 2, "rejected": 3,
         "unreadable-header": 4, "unreadable": 5, "no-expiry-reported": 6,
         "notice": 7, "short-lived": 8, "ok": 9, "unknown": 10}


def schedule(rows):
    """Order the readings by urgency, then by soonest. Pure."""
    def key(row):
        remaining = row.get("seconds_left")
        return (ORDER.get(row.get("state"), 99),
                remaining if isinstance(remaining, int) else 1 << 30,
                str(row.get("name")))
    return sorted(rows or [], key=key)


def verdict(ordered):
    """The one line to act on. Pure."""
    if not ordered:
        return ("nothing-checked",
                "no credentials were named, so nothing was checked.")
    top = ordered[0]
    state = top["state"]
    name = top["name"]
    remaining = top.get("seconds_left")

    if state == "expired":
        return ("expired",
                "%s has already passed its expiry. It will be answering 401 Bad "
                "credentials, identically to a credential that was revoked." % name)
    if state in ("critical", "warning", "notice"):
        return (state,
                "%s expires in %.1f day(s). Alert at 30, 14 and 3 days rather "
                "than at zero." % (name, remaining / DAY))
    if state == "short-lived":
        return ("short-lived",
                "%s expires in %d minute(s), which is what a freshly minted "
                "GitHub App installation token looks like and is a non-event. "
                "It is also what a personal access token in its final two hours "
                "looks like, and the header does not distinguish them."
                % (name, remaining // 60))
    if state == "rejected":
        return ("rejected",
                "%s was refused, so there is no expiry left to forecast. Whether "
                "it expired or was revoked is not observable from here." % name)
    if state in ("unreadable", "unreadable-header"):
        return ("unreadable", "%s could not be read: %s" % (name, top.get("why")))
    if state == "no-expiry-reported":
        return ("no-expiry-reported",
                "%s reported no expiry. Either it never expires, which is a "
                "larger standing risk than one that does, or its class does not "
                "surface a date. Find out which before calling it healthy." % name)
    return ("ok", "the soonest expiry is %s at %.1f day(s)."
            % (name, (remaining or 0) / DAY))


def probe(name, token):
    """One free authenticated GET. Returns (status, headers)."""
    try:
        response = requests.get(
            API + "/rate_limit",
            headers={"Authorization": "Bearer " + token,
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "User-Agent": UA},
            timeout=30)
    except requests.RequestException as exc:
        log.error("%s: request failed: %s", name, exc)
        return 0, {}
    return response.status_code, dict(response.headers)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("env", nargs="*", default=["GITHUB_TOKEN"],
                        help="environment variable names holding credentials")
    parser.add_argument("--notice", type=int, default=DEFAULT_THRESHOLDS[0])
    parser.add_argument("--warning", type=int, default=DEFAULT_THRESHOLDS[1])
    parser.add_argument("--critical", type=int, default=DEFAULT_THRESHOLDS[2])
    args = parser.parse_args()

    thresholds = (args.notice, args.warning, args.critical)
    now = int(time.time())
    rows = []
    for name in (args.env or ["GITHUB_TOKEN"]):
        token = os.environ.get(name)
        if not token:
            rows.append({"name": name, "state": "unreadable", "seconds_left": None,
                         "why": "the environment variable is not set"})
            continue
        status, headers = probe(name, token)
        rows.append(reading(name, status, headers, now, thresholds))

    ordered = schedule(rows)
    for row in ordered:
        remaining = row.get("seconds_left")
        left = "-" if remaining is None else "%.1f day(s)" % (remaining / DAY)
        log.info("%-20s %-20s %12s  %s", row["name"], row["state"], left,
                 row.get("why", ""))

    state, detail = verdict(ordered)
    log.info("%s: %s", state, detail)

    if state in ("critical", "warning", "expired"):
        log.info("repair: rotate now, and record the new expiry in the same "
                 "place the secret is stored so the next person sees it.")
    if state in ("critical", "warning", "notice", "expired", "no-expiry-reported"):
        log.info("repair: for automation with no human owner, authenticate as a "
                 "GitHub App installation. Its tokens are minted on demand, live "
                 "about an hour, and never need a diary entry.")

    print(json.dumps({"state": state, "readings": ordered}, indent=2))
    return 1 if state not in ("ok", "short-lived", "nothing-checked") else 0


if __name__ == "__main__":
    sys.exit(main())
