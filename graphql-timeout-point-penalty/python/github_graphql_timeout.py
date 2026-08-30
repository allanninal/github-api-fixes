"""Measure what a timed-out GraphQL query is charged, without retrying it.

Read only, and queries only. GitHub's GraphQL endpoint takes a document in the
request body, so a read is carried by POST there just as a write would be; that
is transport, not intent. This script sends queries and refuses any document
containing a mutation or a subscription before it opens a socket. Nothing is
written and the repair is printed rather than performed.

GitHub kills a GraphQL request that runs past roughly ten seconds and returns a
502 or 504, and it deducts additional points from the primary rate limit as a
penalty. A timed-out query therefore costs more than a successful one and
returns nothing, which makes a blind retry the most expensive possible response:
the same document against the same data reproduces the timeout and the charge.

What this can and cannot see: GET /rate_limit is free and reports
resources.graphql.used, so the cost of one call is a subtraction. The bucket is
shared by every process holding the token and the API never says which one spent
what, so this script first reads the bucket twice with nothing in between. If it
moves during that gap the measurement is reported as unattributable rather than
as a number.

Environment:

    GITHUB_TOKEN    a token with read access to the GraphQL API
"""
import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_graphql_timeout")

API = "https://api.github.com"
UA = "github-graphql-timeout/1.0"

# The server-side cutoff. Raising the client's own timeout does nothing at all:
# the connection was never the constraint.
TIMEOUT_SECONDS = 10

POINTS_PER_QUERY = 1

# A call using this much of the cutoff is not healthy, it is one busy repository
# away from failing, so it is reported as a finding on a successful run.
NEAR_LIMIT = 0.7

# Substrings GitHub uses when it kills a query for time. Matched case
# insensitively and alongside the status code rather than instead of it.
TIMEOUT_MARKERS = (
    "timeout",
    "timed out",
    "took too long",
    "respond in time",
    "responding in time",
)

# Deliberately heavy: three connections deep with wide slices, which is the
# shape that runs past the cutoff long before it approaches the node limit.
DEFAULT_QUERY = (
    "query($login: String!, $repos: Int = 100, $prs: Int = 40) {"
    " repositoryOwner(login: $login) {"
    " repositories(first: $repos, orderBy: {field: PUSHED_AT, direction: DESC}) {"
    " nodes { name pullRequests(first: $prs, states: OPEN) {"
    " nodes { number title comments(first: 20) { totalCount nodes { createdAt } } }"
    " } } } } }"
)


def strip_noise(document):
    """Remove GraphQL comments and string literals from a document. Pure."""
    src = str(document or "")
    out = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch == "#":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if src.startswith('"""', i):
            j = src.find('"""', i + 3)
            i = n if j < 0 else j + 3
            out.append(" ")
            continue
        if ch == '"':
            i += 1
            while i < n and src[i] != '"':
                i += 2 if src[i] == "\\" else 1
            i += 1
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def operations(document):
    """The top-level operations in a document, in order. Pure."""
    src = strip_noise(document)
    ops, depth, word, declared = [], 0, "", None
    for ch in src + " ":
        if ch.isalnum() or ch == "_":
            word += ch
            continue
        if word:
            if depth == 0 and word in ("query", "mutation", "subscription", "fragment"):
                declared = word
            word = ""
        if ch == "{":
            if depth == 0:
                ops.append(declared or "query")
                declared = None
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
    return ops


def refusal(document):
    """Why this document will not be sent, or None if it is a read. Pure."""
    ops = operations(document)
    if not ops:
        return "the document contains no operation to send."
    for kind in ("mutation", "subscription"):
        if kind in ops:
            return ("the document contains a %s. This script sends queries only: "
                    "a query is a read, and the section it belongs to promises "
                    "its scripts never write." % kind)
    return None


def bucket_reading(payload, name="graphql"):
    """One bucket out of a GET /rate_limit body. Pure.

    The GraphQL bucket, not the core one. Reading the wrong bucket here would
    produce a measurement that never moves and a note that concludes there is no
    penalty.
    """
    if not isinstance(payload, dict):
        return None
    resources = payload.get("resources")
    if not isinstance(resources, dict):
        return None
    bucket = resources.get(name)
    if not isinstance(bucket, dict):
        return None
    return {"limit": bucket.get("limit"), "used": bucket.get("used"),
            "remaining": bucket.get("remaining"), "reset": bucket.get("reset")}


def charged(before, after):
    """Points spent between two readings. Pure. Returns (points, state).

    A reset timestamp that moved means the hourly window rolled over between the
    readings and the subtraction is meaningless, which is worth detecting rather
    than reporting as a negative number or as zero.
    """
    if not isinstance(before, dict) or not isinstance(after, dict):
        return (None, "unreadable")
    if before.get("reset") != after.get("reset"):
        return (None, "window-reset")
    start, end = before.get("used"), after.get("used")
    if not isinstance(start, int) or not isinstance(end, int):
        return (None, "unreadable")
    if end < start:
        return (None, "window-reset")
    return (end - start, "measured")


def net_charge(delta, background):
    """The charge with a known background drain removed. Pure."""
    if not isinstance(delta, int):
        return None
    if not isinstance(background, int) or background <= 0:
        return delta
    return max(0, delta - background)


def timeout_message(body):
    """The message GitHub returned, or None. Pure."""
    if not isinstance(body, dict):
        return None
    errors = body.get("errors")
    if isinstance(errors, list):
        for err in errors:
            if isinstance(err, dict) and err.get("message"):
                return str(err["message"])
    if body.get("message"):
        return str(body["message"])
    return None


def looks_like_timeout(status, body):
    """Whether this response is the server giving up on time. Pure."""
    if status in (502, 504):
        return True
    message = (timeout_message(body) or "").lower()
    return any(marker in message for marker in TIMEOUT_MARKERS)


def timing_consistent(elapsed):
    """Whether the elapsed time agrees with the documented cutoff. Pure."""
    if not isinstance(elapsed, (int, float)):
        return False
    return elapsed >= TIMEOUT_SECONDS * 0.8


def headroom(elapsed):
    """How much of the cutoff this call used, as a fraction. Pure."""
    if not isinstance(elapsed, (int, float)) or elapsed < 0:
        return None
    return elapsed / float(TIMEOUT_SECONDS)


def penalty(points, normal_cost):
    """Points charged above what the query would have cost. Pure."""
    if not isinstance(points, int) or not isinstance(normal_cost, int):
        return None
    return points - normal_cost


def retry_projection(points, retries):
    """What retrying this document would spend for nothing. Pure."""
    if not isinstance(points, int) or not retries or retries < 1:
        return 0
    return points * int(retries)


def classify(status, elapsed, points, normal_cost, background=0, body=None):
    """Classify one attempt. Pure. Returns (state, detail)."""
    timed_out = looks_like_timeout(status, body)
    if points is None:
        return ("charge-not-measurable",
                "the two rate-limit readings do not support a subtraction, so "
                "what this call cost cannot be stated%s."
                % (" -- and it did time out" if timed_out else ""))
    if timed_out and isinstance(background, int) and background > 0:
        return ("timed-out-charge-not-attributable",
                "the query was killed and %d point(s) moved, but the bucket was "
                "already draining with nothing sent, so the charge belongs to "
                "more than this call." % points)
    extra = penalty(points, normal_cost)
    if timed_out and isinstance(extra, int) and extra > 0:
        return ("timed-out-and-charged-extra",
                "the query was killed at the %ds cutoff and cost %d point(s) "
                "against a normal cost of %d, a penalty of %d point(s)."
                % (TIMEOUT_SECONDS, points, normal_cost, extra))
    if timed_out:
        return ("timed-out-charge-not-proved",
                "the query was killed at the %ds cutoff and the bucket moved by "
                "%d point(s), which is not more than its normal cost. The "
                "timeout is real; the penalty is not demonstrated by this run."
                % (TIMEOUT_SECONDS, points))
    fraction = headroom(elapsed)
    if fraction is not None and fraction >= NEAR_LIMIT:
        return ("close-to-the-timeout",
                "the query returned, in %.1fs, which is %d%% of the %ds cutoff. "
                "This one is one busy repository away from the failure above."
                % (elapsed, round(fraction * 100), TIMEOUT_SECONDS))
    return ("completed-inside-the-limit",
            "the query returned in %.1fs and was charged %d point(s), which is "
            "the ordinary case." % (elapsed or 0.0, points))


def repair(state):
    """The sentence a reader has to act on. Pure."""
    if state == "timed-out-and-charged-extra":
        return ("lower the first values and split the nested connections. Do "
                "not retry a timed-out query: the same document reproduces the "
                "timeout and the penalty.")
    if state == "timed-out-charge-not-proved":
        return ("make the query smaller anyway. The timeout is the finding; "
                "whether this particular run demonstrated the extra charge does "
                "not change the repair.")
    if state == "timed-out-charge-not-attributable":
        return ("re-run this when nothing else is holding the token, or give "
                "the job its own token. A shared bucket cannot attribute a "
                "charge to a call.")
    if state == "charge-not-measurable":
        return ("re-run it away from the top of the hour, when the window is "
                "less likely to reset between the two readings.")
    if state == "close-to-the-timeout":
        return ("shrink it now rather than after the outage. Fewer nested "
                "connections and lower first values, paginated.")
    if state == "completed-inside-the-limit":
        return ("nothing here. Keep the elapsed time in your logs so the day it "
                "starts climbing is visible before the day it fails.")
    return "point the check at a document this endpoint can answer."


def point_cost(queries):
    """Points this run will spend before any penalty. Pure."""
    return int(queries or 0) * POINTS_PER_QUERY


def read_bucket(session):
    """GET /rate_limit, which is free and does not consume quota."""
    r = session.get(API + "/rate_limit", timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed "
                         "or revoked")
    try:
        return bucket_reading(r.json())
    except ValueError:
        return None


def run_query(session, document, variables):
    """Send one query, once. Returns (status, body-or-None, elapsed).

    A GraphQL query is a read; POST is only how the document reaches the
    endpoint, which is why the verb is written here beside the URL rather than
    tucked into a constant where it could be mistaken for a write path. There is
    no retry here by design: retrying is the behaviour this script exists to
    stop.
    """
    started = time.monotonic()
    r = session.post(API + "/graphql",
                     json={"query": document, "variables": variables or {}},
                     timeout=60)
    elapsed = time.monotonic() - started
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed "
                         "or revoked")
    try:
        return r.status_code, r.json(), elapsed
    except ValueError:
        return r.status_code, {"message": r.text[:300]}, elapsed


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--login", help="user or organisation for the default query")
    ap.add_argument("--file", help="a .graphql file to send instead")
    ap.add_argument("--query", help="the document as a string")
    ap.add_argument("--variables", default="{}", help="JSON object of variables")
    ap.add_argument("--normal-cost", type=int, default=1,
                    help="what this query costs when it finishes. Measure it "
                         "with the cost note rather than guessing.")
    ap.add_argument("--retries", type=int, default=3,
                    help="how many retries to price. Nothing is retried.")
    args = ap.parse_args()

    document = Path(args.file).read_text(encoding="utf-8") if args.file \
        else (args.query or DEFAULT_QUERY)
    try:
        variables = json.loads(args.variables)
    except ValueError:
        log.error("--variables takes a JSON object")
        return 2
    if not isinstance(variables, dict):
        log.error("--variables takes a JSON object")
        return 2
    if not args.file and not args.query:
        if not args.login:
            log.error("--login takes a user or organisation name")
            return 2
        variables.setdefault("login", args.login)

    why_not = refusal(document)
    if why_not:
        log.error("refusing to send: %s", why_not)
        return 2

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })

    log.info("point cost: up to %d point(s) for the query plus whatever the "
             "timeout penalty adds, which is the number this run measures. Both "
             "/rate_limit reads are free.", point_cost(1))

    idle_before = read_bucket(session)
    idle_after = read_bucket(session)
    background, idle_state = charged(idle_before, idle_after)
    if idle_state == "measured":
        log.info("idle check: graphql used %s -> %s with nothing sent, so the "
                 "bucket is %s", idle_before.get("used"), idle_after.get("used"),
                 "quiet" if background == 0 else "already draining")
    else:
        log.info("idle check: %s, so the background drain is unknown", idle_state)
        background = 0

    log.info("sending one query. This script never retries a timed-out query.")
    before = idle_after
    status, body, elapsed = run_query(session, document, variables)
    after = read_bucket(session)

    message = timeout_message(body)
    log.info("HTTP %s after %.1fs%s", status, elapsed,
             ": %s" % message[:160] if message else "")
    delta, state_of_charge = charged(before, after)
    points = net_charge(delta, background)
    if state_of_charge == "measured":
        log.info("graphql used %s -> %s, so this call was charged %s point(s)",
                 before.get("used"), after.get("used"), points)
    else:
        log.info("the charge could not be measured: %s", state_of_charge)

    state, detail = classify(status, elapsed, points, args.normal_cost,
                             background, body)
    log.info("%s: %s", state, detail)
    if timing_consistent(elapsed) and looks_like_timeout(status, body):
        log.info("the elapsed time agrees with the documented %ds cutoff",
                 TIMEOUT_SECONDS)
    projected = retry_projection(points, args.retries)
    if projected and state.startswith("timed-out"):
        log.info("%d retries of this document would spend %d more point(s) and "
                 "return nothing", args.retries, projected)
    log.info("repair: %s", repair(state))

    print(json.dumps({"status": status, "elapsed_seconds": round(elapsed, 2),
                      "headroom": headroom(elapsed), "charged": points,
                      "background_drain": background,
                      "normal_cost": args.normal_cost,
                      "penalty": penalty(points, args.normal_cost),
                      "retry_cost": projected, "state": state,
                      "detail": detail}, indent=2, default=str))
    return 1 if state.startswith("timed-out") or state == "close-to-the-timeout" else 0


if __name__ == "__main__":
    sys.exit(main())
