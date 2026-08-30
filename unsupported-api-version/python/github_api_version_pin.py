"""Check the X-GitHub-Api-Version your client pins against the versions GitHub serves.

Read only, and mostly unauthenticated: GET /versions needs no credential and
returns the list of date-based REST API versions currently supported. Nothing
here changes a header, a deployment or a pin. The repair is printed.

The point of running this on a schedule rather than during an incident is that
a version leaves the supported list before it starts refusing requests. The
state worth alerting on is "supported but behind", which is the same problem
with months of notice attached.
"""
import argparse
import json
import logging
import os
import re
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_api_version_pin")

API = "https://api.github.com"
UA = "github-api-version-pin/1.0"

# The version a request gets when it carries no X-GitHub-Api-Version header.
# Unpinned is not unversioned: this value has a lifetime like any other.
SERVER_DEFAULT = "2022-11-28"

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# What a refusal about the version looks like in prose. Matched on words rather
# than on a status code, which has been reported as more than one number and is
# not something a check should depend on.
VERSION_WORDS = re.compile(
    r"api version|x-github-api-version|version.*(not supported|no longer)",
    re.IGNORECASE)


def is_version(value):
    """Whether a string is shaped like an API version. Pure.

    A date shape is necessary and not sufficient: 2022-11-38 passes the regex
    and is not a date, so the day and month are range checked too. A typo that
    looks like a version fails differently from one that does not.
    """
    text = str(value or "").strip()
    if not DATE.match(text):
        return False
    year, month, day = (int(p) for p in text.split("-"))
    return 2000 <= year <= 2999 and 1 <= month <= 12 and 1 <= day <= 31


def supported(body):
    """Parse the GET /versions body into a sorted list of versions. Pure.

    Anything that is not version-shaped is dropped rather than carried, because
    a junk entry that sorts to the end would make every real pin look retired.
    Date strings sort correctly as text, which is the one convenience of this
    format.
    """
    if not isinstance(body, list):
        return []
    return sorted({str(v).strip() for v in body if is_version(v)})


def behind(pin, versions):
    """Supported versions strictly newer than the pin. Pure.

    These are the breaking-change notes to read before moving, so the count
    matters as much as the target.
    """
    return [v for v in versions or [] if v > str(pin or "")]


def nearest(pin, versions):
    """The supported version closest to a pin, for the typo case. Pure.

    Compared as digit strings rather than parsed as dates, which is enough to
    order them and avoids pretending 2022-11-38 is a calendar date in order to
    say it is not one.
    """
    if not versions:
        return None
    target = re.sub(r"\D", "", str(pin or "")) or "0"
    return min(versions, key=lambda v: (abs(int(re.sub(r"\D", "", v)) - int(target)), v))


def classify(pin, versions):
    """Sort a pinned value into one of six states. Pure.

    Five of them are about the pin. The sixth is about not being able to judge
    at all, which is a real outcome when GET /versions is what is unreachable.
    """
    if not versions:
        return ("no-versions-list",
                "GET /versions returned nothing version-shaped, so the pin "
                "cannot be judged. That is a failure of the check rather than "
                "a finding about the pin.")

    newest = versions[-1]
    if pin is None or not str(pin).strip():
        state = "unpinned"
        detail = ("no X-GitHub-Api-Version header is sent, so requests get "
                  "GitHub's default of %s. That is a real version with a real "
                  "lifetime: unpinned means pinned by the server, and it moves "
                  "without asking." % SERVER_DEFAULT)
        if SERVER_DEFAULT not in versions:
            detail += (" The default this script knows about is not on the "
                       "served list any more, so check what the current one is.")
        return state, detail

    pin = str(pin).strip()
    if not is_version(pin):
        return ("malformed-pin",
                "%r is not shaped like an API version. It was never valid, so "
                "this is a typo rather than a retirement; the closest served "
                "version is %s." % (pin, nearest(pin, versions)))
    if pin in versions:
        newer = behind(pin, versions)
        if not newer:
            return ("supported-current",
                    "%s is the newest version GitHub serves." % pin)
        return ("supported-behind",
                "%s is still served, and %d newer version(s) exist: %s. This "
                "is the state to alert on, because it is this problem with "
                "notice attached." % (pin, len(newer), ", ".join(newer)))
    if pin < versions[0]:
        return ("retired",
                "%s is older than every supported version. Requests pinned to "
                "it are refused, and the oldest one still served is %s."
                % (pin, versions[0]))
    if pin > newest:
        return ("not-yet-supported",
                "%s is newer than every supported version, so it names a "
                "version that does not exist yet. Almost always a typo; the "
                "closest served version is %s." % (pin, nearest(pin, versions)))
    return ("unknown-version",
            "%s is a valid date and was never a published version. The "
            "closest served version is %s." % (pin, nearest(pin, versions)))


def confirms_version_refusal(status, message):
    """Whether a live response blames the API version. Pure.

    Keyed on the words rather than the status code. A refusal about the version
    is unambiguous in prose and has been reported under more than one number,
    so matching the number is how a check quietly stops working.
    """
    if status is None or status < 400:
        return False
    return bool(VERSION_WORDS.search(str(message or "")))


def get(session, path):
    """One GET. Returns (status, json-or-None)."""
    r = session.get(API + path, timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pinned", default=os.environ.get("GITHUB_API_VERSION"),
                    help="the value your client sends in X-GitHub-Api-Version; "
                         "omit it to check the unpinned case")
    ap.add_argument("--path", default="/meta",
                    help="a path to re-send with the pin, to confirm the "
                         "verdict live")
    args = ap.parse_args()

    session = requests.Session()
    session.headers.update({
        "Accept": "application/vnd.github+json",
        "User-Agent": UA,
    })
    # Optional. GET /versions is public; a token only raises the rate limit
    # this check shares with every other anonymous caller on the address.
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        session.headers["Authorization"] = "Bearer " + token

    status, body = get(session, "/versions")
    if status != 200:
        log.error("GET /versions returned %d, so there is no list to compare "
                  "against", status)
        return 2
    versions = supported(body)
    log.info("supported: %s", ", ".join(versions) or "nothing version-shaped")

    state, detail = classify(args.pinned, versions)
    log.info("%s: %s", state, detail)

    # Confirmation, not diagnosis: send the pin at a cheap path and see whether
    # the response blames it.
    if args.pinned:
        session.headers["X-GitHub-Api-Version"] = str(args.pinned)
        live_status, live_body = get(session, args.path)
        message = live_body.get("message") if isinstance(live_body, dict) else None
        log.info("%s with the pin returned %d", args.path, live_status)
        if confirms_version_refusal(live_status, message):
            log.info("confirmed live: the response blames the version")
        elif live_status >= 400:
            log.info("the %d does not mention the version, so it has another "
                     "cause", live_status)

    if state in ("retired", "unknown-version", "not-yet-supported",
                 "malformed-pin"):
        target = versions[-1]
        log.info("repair: move the pin to %s, reading the notes for %d "
                 "version(s) in between first", target,
                 len(behind(args.pinned or "", versions)) - 1
                 if args.pinned else 0)
    if state == "supported-behind":
        log.info("repair: schedule the move to %s; nothing is failing yet, "
                 "which is the only good time to do it", versions[-1])

    print(json.dumps({"pinned": args.pinned, "supported": versions,
                      "behind_by": len(behind(args.pinned or "", versions)),
                      "state": state}, indent=2))
    return 1 if state in ("retired", "unknown-version", "not-yet-supported",
                          "malformed-pin") else 0


if __name__ == "__main__":
    sys.exit(main())
