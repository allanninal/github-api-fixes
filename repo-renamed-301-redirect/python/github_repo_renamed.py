"""Find repositories whose configured name is a redirect to somewhere else.

Read only. At most two GETs per repository: one with redirects disabled, and
one to follow the redirect where there is one. Nothing is written and the
repair is printed rather than performed.

Renaming or transferring a repository leaves a permanent redirect at the old
path. A client that does not follow redirects sees a 301 with an empty body and
reports the repository as missing; a client that does follow them works
perfectly and pays an extra round trip on every call, forever, while the
configured name rots.

What this can and cannot see: whether your own client follows redirects is
invisible from here, so both consequences are reported and you recognise yours.
Everything else about this failure is fully readable, which is unusual.

Environment:

    GITHUB_TOKEN    a token with read access to the repository
"""
import argparse
import json
import logging
import os
import re
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_repo_renamed")

API = "https://api.github.com"
UA = "github-repo-renamed/1.0"

# 301 and 308 say the address changed. 302 and 307 say the routing changed.
# The difference is the whole reason this script has more than two states.
PERMANENT = (301, 308)
TEMPORARY = (302, 307)

# The canonical URL is usually the numeric form, which hands you the durable
# key rather than the new name.
LOC_ID = re.compile(r"/repositories/(\d+)")
LOC_FULL = re.compile(r"/repos/([^/?#]+)/([^/?#]+)")


def is_redirect(status):
    """Whether this status moves you somewhere else. Pure."""
    try:
        return int(status) in PERMANENT + TEMPORARY
    except (TypeError, ValueError):
        return False


def is_permanent(status):
    """Whether this status means update your code rather than follow it. Pure."""
    try:
        return int(status) in PERMANENT
    except (TypeError, ValueError):
        return False


def repo_from_location(location):
    """What the Location header points at. Pure. Returns (kind, value) or None.

    Two shapes, and the numeric one is the common answer: GitHub redirects a
    renamed repository to /repositories/{id}, which is the identifier that
    survives every future rename.
    """
    if not location:
        return None
    text = str(location)
    m = LOC_ID.search(text)
    if m:
        return ("id", m.group(1))
    m = LOC_FULL.search(text)
    if m:
        return ("full_name", "%s/%s" % (m.group(1), m.group(2)))
    return None


def same_repo(a, b):
    """Whether two owner/name strings name the same repository. Pure.

    Case-insensitively, because GitHub matches names that way and a comparison
    that does not manufactures a rename nobody performed.
    """
    if not a or not b:
        return False
    return str(a).strip().lower() == str(b).strip().lower()


def verdict(asked, status, location=None, full_name=None):
    """Classify one probe of a configured repository name. Pure."""
    try:
        code = int(status)
    except (TypeError, ValueError):
        return ("unknown", "the probe produced no readable status.")

    if is_permanent(code):
        target = repo_from_location(location)
        if not target:
            return ("renamed-permanent",
                    "%d says the configured name is stale, but the response "
                    "carried no usable Location, so the new name has to be read "
                    "from the body after following it once." % code)
        kind, value = target
        named = (", now called %s" % full_name) if full_name else ""
        if kind == "id":
            return ("renamed-permanent",
                    "the configured name is stale and GitHub is redirecting it "
                    "permanently to repository id %s%s." % (value, named))
        return ("renamed-permanent",
                "the configured name is stale and GitHub is redirecting it "
                "permanently to %s%s." % (value, named))

    if is_redirect(code):
        return ("moved-temporary",
                "%d is a temporary redirect, so follow it and change nothing. "
                "Writing this address into your configuration is the mistake "
                "here, not the fix." % code)

    if code == 404:
        return ("not-found",
                "404 is not a rename. It means no repository, no permission, no "
                "installation or a dead token, and separating those four is a "
                "different check.")

    if code != 200:
        return ("unknown",
                "%d is neither a redirect nor a readable repository." % code)

    if not full_name:
        return ("unknown",
                "the repository was returned without a full_name, so there is "
                "nothing to compare the configured name against.")

    if str(asked).strip() == str(full_name).strip():
        return ("current",
                "the configured name matches full_name and the request was "
                "answered without a redirect.")

    if same_repo(asked, full_name):
        return ("case-only",
                "the configured name differs from %s only in capitalisation. "
                "GitHub matches names case-insensitively, so this is the same "
                "repository and there is nothing to do." % full_name)

    return ("renamed-followed",
            "the request was answered as %s rather than as the name that was "
            "asked for, so a redirect was followed somewhere between here and "
            "GitHub and nobody was told." % full_name)


def durable_key(repo):
    """The identifiers that survive a rename. Pure. None when absent."""
    if not isinstance(repo, dict):
        return None
    key = {k: repo.get(k) for k in ("id", "node_id") if repo.get(k) is not None}
    return key or None


def extra_round_trips(calls):
    """Requests a followed redirect adds over a period. Pure.

    One per call: the redirect itself is a request, and it buys nothing except
    an address you could have written down once.
    """
    try:
        return max(0, int(calls))
    except (TypeError, ValueError):
        return 0


def repair(state):
    """The sentence a reader has to act on. Pure."""
    if state == "renamed-permanent":
        return ("update the stored owner/name to the value in the Location or "
                "in full_name, and key persistent state on the repository id or "
                "node_id, which survive the next rename too.")
    if state == "renamed-followed":
        return ("your client is following a redirect silently. Update the "
                "configured name to the full_name that came back, and key "
                "persistent state on id or node_id so the next rename is free.")
    if state == "moved-temporary":
        return ("follow it and change nothing. A temporary redirect is routing "
                "and does not belong in your configuration.")
    if state == "case-only":
        return ("nothing. The names differ only in capitalisation and GitHub "
                "matches them case-insensitively.")
    if state == "not-found":
        return ("triage the 404 rather than assuming a rename: check the token, "
                "the scopes and the installation before the name.")
    if state == "current":
        return "nothing."
    return "point the check at a repository this token can read."


def read_cost(repos):
    """Requests this run will spend against the core quota. Pure.

    An upper bound: one probe per repository, plus one more only where there is
    a redirect to follow.
    """
    return 2 * len(repos or [])


def probe(session, full_name):
    """One GET with redirects disabled. Returns (status, location)."""
    r = session.get(API + "/repos/" + full_name, allow_redirects=False, timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed "
                         "or revoked")
    if r.status_code == 403 and "rate limit" in r.text.lower():
        raise SystemExit("403 rate limited. GET /rate_limit reports the reset "
                         "time and does not itself consume quota")
    return r.status_code, r.headers.get("Location")


def resolve(session, url):
    """Follow one redirect and read the repository object. Returns dict or None."""
    r = session.get(url, timeout=30)
    if r.status_code != 200:
        return None
    try:
        body = r.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", action="append", required=True,
                    help="owner/name as your configuration has it. Repeatable.")
    ap.add_argument("--calls-per-hour", type=int, default=0,
                    help="how often your integration calls this repository, so "
                         "the cost of a followed redirect can be stated in "
                         "requests rather than in adjectives")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    log.info("read cost: at most %d request(s) per repository against the core "
             "hourly quota", 2)
    log.info("read cost: %d request(s) in total at most", read_cost(args.repo))

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })

    findings = []
    for name in args.repo:
        status, location = probe(session, name)
        repo = None
        if is_redirect(status) and location:
            log.info("%s: %d -> %s", name, status, location)
            repo = resolve(session, location)
        elif status == 200:
            repo = resolve(session, API + "/repos/" + name)

        full_name = (repo or {}).get("full_name")
        state, detail = verdict(name, status, location, full_name)
        log.info("%s: %s", state, detail)
        log.info("repair: %s", repair(state))
        if state in ("renamed-permanent", "renamed-followed") and args.calls_per_hour:
            log.info("a client that follows this pays 1 extra request per call: "
                     "%d calls an hour becomes %d", args.calls_per_hour,
                     args.calls_per_hour + extra_round_trips(args.calls_per_hour))

        findings.append({
            "configured": name,
            "status": status,
            "location": location,
            "location_points_at": repo_from_location(location),
            "full_name": full_name,
            "durable_key": durable_key(repo),
            "extra_requests_per_hour": (
                extra_round_trips(args.calls_per_hour)
                if state in ("renamed-permanent", "renamed-followed") else 0),
            "state": state,
            "detail": detail,
            "repair": repair(state),
        })

    print(json.dumps({"requests_spent_at_most": read_cost(args.repo),
                      "findings": findings}, indent=2, default=str))
    bad = {"renamed-permanent", "renamed-followed"}
    return 1 if any(f["state"] in bad for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
