"""Read the GraphQL point budget, which is not the REST one.

Read only. The default run spends nothing at all: GET /rate_limit reports both
buckets and is documented not to count against either. The optional in-band
probe sends one query and costs one point, and it says so first.

Queries only. GitHub's GraphQL endpoint takes a document in the request body, so
a read is carried by POST there just as a write would be; that is transport, not
intent. Any document containing a mutation or a subscription is refused before a
socket opens. Nothing is written and the repair is printed rather than performed.

GraphQL is billed in points from its own hourly budget: 5,000 for a user token,
1,000 for the GITHUB_TOKEN inside GitHub Actions, 10,000 on Enterprise Cloud.
The REST core bucket is untouched by GraphQL traffic and vice versa, which is why
a REST health check reports green while every GraphQL call is failing.

What this can and cannot see: the bucket is shared by every process using the
token and the API never says which one spent it. A drain you cannot account for
is a reason to issue a separate token per workload, not a query to tune.

Environment:

    GITHUB_TOKEN    a token with read access to the GraphQL API
"""
import argparse
import json
import logging
import os
import sys
import time

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_graphql_points")

API = "https://api.github.com"
UA = "github-graphql-points/1.0"

# The published hourly point budgets, keyed by the actor they belong to. Read
# backwards, from an observed limit to the actor, this identifies a job that is
# running as something other than what its author assumed.
BUDGETS = {
    5000: "a user token",
    1000: "the GITHUB_TOKEN issued to a GitHub Actions workflow",
    10000: "an Enterprise Cloud token",
}

# The smallest useful in-band probe. Asking for cost alone would not report the
# node count, and nodeCount is the number the query-shape note needs.
BUDGET_QUERY = "query { rateLimit { limit cost remaining used resetAt nodeCount } }"

# Below this fraction of budget left, slow down rather than discover zero.
TIGHT = 0.2


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


def bucket(rate_limit_body, name):
    """One resource object out of GET /rate_limit. Pure. None if absent."""
    if not isinstance(rate_limit_body, dict):
        return None
    res = rate_limit_body.get("resources")
    if not isinstance(res, dict):
        return None
    b = res.get(name)
    return b if isinstance(b, dict) else None


def used_fraction(b):
    """How much of a bucket is gone, 0.0 to 1.0. Pure. None if unreadable."""
    if not isinstance(b, dict):
        return None
    try:
        limit = int(b.get("limit"))
        remaining = int(b.get("remaining"))
    except (TypeError, ValueError):
        return None
    if limit <= 0:
        return None
    return max(0.0, min(1.0, (limit - remaining) / float(limit)))


def seconds_to_reset(b, now):
    """Seconds until this bucket refills. Pure. None if unreadable."""
    if not isinstance(b, dict):
        return None
    try:
        return max(0, int(b.get("reset")) - int(now))
    except (TypeError, ValueError):
        return None


def identify_budget(limit):
    """Which actor an observed hourly limit implies. Pure."""
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return "an unreadable limit"
    if n in BUDGETS:
        return BUDGETS[n]
    return ("a limit of %d, which matches none of the published budgets. Read "
            "it as the truth and plan against it." % n)


def queries_left(remaining, cost_per_query):
    """How many more queries of this shape fit in what is left. Pure."""
    try:
        rem = int(remaining)
        cost = int(cost_per_query)
    except (TypeError, ValueError):
        return None
    if cost <= 0:
        return None
    return max(0, rem // cost)


def sustainable_rate(limit, cost_per_query):
    """Queries per hour this budget supports at a measured cost. Pure."""
    try:
        lim = int(limit)
        cost = int(cost_per_query)
    except (TypeError, ValueError):
        return None
    if cost <= 0 or lim <= 0:
        return None
    return lim // cost


def seconds_between(limit, cost_per_query):
    """The gap to leave between queries to stay inside the budget. Pure."""
    rate = sustainable_rate(limit, cost_per_query)
    if not rate:
        return None
    return round(3600.0 / rate, 1)


def error_types(body):
    """The type of every entry in a GraphQL errors array. Pure."""
    if not isinstance(body, dict):
        return []
    return [(e.get("type") or "UNTYPED") if isinstance(e, dict) else "UNTYPED"
            for e in (body.get("errors") or [])]


def is_rate_limited(body):
    """Whether a GraphQL envelope reports the budget as spent. Pure."""
    return "RATE_LIMITED" in error_types(body)


def in_band_cost(body):
    """The cost this query reported for itself. Pure. None if not asked for."""
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    if not isinstance(data, dict):
        return None
    rl = data.get("rateLimit")
    if not isinstance(rl, dict):
        return None
    try:
        return int(rl.get("cost"))
    except (TypeError, ValueError):
        return None


def classify(graphql_b, core_b):
    """Compare the two buckets. Pure. Returns (state, detail).

    The point of comparing rather than reporting one is that the confusing case
    has a shape: one bucket empty, the other nearly full, same token.
    """
    g = used_fraction(graphql_b)
    c = used_fraction(core_b)
    if g is None:
        return ("unreadable",
                "resources.graphql was not present in the response, so the "
                "GraphQL budget cannot be read from it.")
    g_left = 1.0 - g
    c_left = 1.0 - c if c is not None else None
    g_empty = (graphql_b or {}).get("remaining") == 0
    c_empty = c_left is not None and (core_b or {}).get("remaining") == 0

    if g_empty and c_empty:
        return ("both-exhausted",
                "both buckets are empty, so this is not the confusing case: "
                "everything fails and everything is meant to.")
    if g_empty:
        return ("graphql-exhausted-rest-healthy",
                "the GraphQL bucket is empty while core is at %d%% remaining, so "
                "a REST health check reports green on a dead integration."
                % round((c_left or 0) * 100))
    if c_empty:
        return ("rest-exhausted-graphql-healthy",
                "core is empty and the GraphQL budget is at %d%% remaining. That "
                "is the REST hourly quota, not this one."
                % round(g_left * 100))
    if g_left < TIGHT:
        return ("graphql-tight",
                "%d%% of the GraphQL budget is left, which is close enough that "
                "the next burst decides it." % round(g_left * 100))
    return ("both-healthy",
            "%d%% of the GraphQL budget and %s of core are left."
            % (round(g_left * 100),
               "an unknown amount" if c_left is None else "%d%%" % round(c_left * 100)))


def repair(state):
    """The sentence a reader has to act on. Pure."""
    if state == "graphql-exhausted-rest-healthy":
        return ("throttle on resources.graphql.remaining, not on core. Add "
                "rateLimit { cost remaining } to your real queries so each one "
                "reports its own price, and point the health check at the bucket "
                "the traffic actually spends.")
    if state == "graphql-tight":
        return ("slow down now rather than at zero. Divide the remaining points "
                "by the measured cost of your query to get the number of calls "
                "you have left, and space them out.")
    if state == "rest-exhausted-graphql-healthy":
        return ("see /github/rate-limit-core-exhausted/ -- this is the REST "
                "hourly quota and the repair for it is conditional requests and "
                "webhooks, not point budgeting.")
    if state == "both-exhausted":
        return ("wait for the resets and then fix them separately: they refill "
                "on their own schedules and neither repair helps the other.")
    if state == "both-healthy":
        return ("nothing today. Measure the cost of your query anyway, because "
                "the budget in queries is what you schedule against and it is "
                "not 5,000.")
    return "read GET /rate_limit with a token this API accepts."


def point_cost(in_band):
    """Points this run will spend. Pure. Zero unless the in-band probe is asked for."""
    return 1 if in_band else 0


def fmt_reset(seconds):
    """A reset delay in something readable. Pure."""
    if seconds is None:
        return "unknown"
    if seconds < 90:
        return "%ds" % seconds
    return "%dm" % round(seconds / 60.0)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in-band", action="store_true",
                    help="spend one point sending { rateLimit { ... } } to "
                         "measure a query cost directly")
    ap.add_argument("--query",
                    help="measure this document's cost instead of the minimal "
                         "probe. Add rateLimit { cost remaining } to it first. "
                         "Mutations are refused.")
    ap.add_argument("--cost", type=int, default=1,
                    help="the cost of one of your queries, if you already know "
                         "it, used to convert points into queries")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    document = args.query or BUDGET_QUERY
    if args.in_band:
        why_not = refusal(document)
        if why_not:
            log.error("refusing to send: %s", why_not)
            return 2
        log.info("point cost: %d point(s) against the 5,000/hour GraphQL budget",
                 point_cost(True))
    else:
        log.info("point cost: %d point(s). GET /rate_limit reports the GraphQL "
                 "bucket and does not consume any of it.", point_cost(False))

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "User-Agent": UA,
    })

    r = session.get(API + "/rate_limit", timeout=30)
    if r.status_code == 401:
        log.error("401 from GitHub: GITHUB_TOKEN is missing, malformed or revoked")
        return 2
    body = r.json() if r.status_code == 200 else None
    graphql_b = bucket(body, "graphql")
    core_b = bucket(body, "core")
    now = int(time.time())

    for name, b in (("core", core_b), ("graphql", graphql_b)):
        if b is None:
            log.info("%-8s not reported", name)
            continue
        log.info("%-8s %s / %s remaining, resets in %s",
                 name, b.get("remaining"), b.get("limit"),
                 fmt_reset(seconds_to_reset(b, now)))
    if graphql_b:
        log.info("budget: a limit of %s points/hour is %s",
                 graphql_b.get("limit"), identify_budget(graphql_b.get("limit")))

    state, detail = classify(graphql_b, core_b)
    log.info("%s: %s", state, detail)

    measured = args.cost
    envelope = None
    if args.in_band:
        # A GraphQL query is a read; POST is only how the document reaches the
        # endpoint, which is why the verb sits here beside the URL rather than
        # in a constant where it could be mistaken for a write path.
        resp = session.post(API + "/graphql", json={"query": document}, timeout=30)
        try:
            envelope = resp.json()
        except ValueError:
            envelope = None
        if is_rate_limited(envelope):
            log.info("the in-band probe itself came back RATE_LIMITED, which is "
                     "the finding stated by the endpoint rather than inferred")
        cost = in_band_cost(envelope)
        if cost is not None:
            measured = cost
            log.info("measured cost: %d point(s) for this query shape", cost)
        else:
            log.info("the response carried no rateLimit.cost; add "
                     "rateLimit { cost remaining } to the document")

    limit = (graphql_b or {}).get("limit")
    remaining = (graphql_b or {}).get("remaining")
    rate = sustainable_rate(limit, measured)
    gap = seconds_between(limit, measured)
    if rate:
        log.info("at %d points a query the budget is %d queries/hour, one every %ss",
                 measured, rate, gap)
    left = queries_left(remaining, measured)
    if left is not None:
        log.info("%s point(s) left is %d more quer%s of this shape",
                 remaining, left, "y" if left == 1 else "ies")
    log.info("repair: %s", repair(state))

    print(json.dumps({
        "points_spent": point_cost(args.in_band),
        "graphql": graphql_b,
        "core": core_b,
        "graphql_used_fraction": used_fraction(graphql_b),
        "core_used_fraction": used_fraction(core_b),
        "budget_identified_as": identify_budget(limit),
        "measured_cost": measured,
        "queries_per_hour": rate,
        "seconds_between_queries": gap,
        "queries_left": left,
        "in_band_rate_limited": is_rate_limited(envelope),
        "state": state,
        "detail": detail,
    }, indent=2, default=str))
    bad = {"graphql-exhausted-rest-healthy", "graphql-tight", "both-exhausted"}
    return 1 if state in bad else 0


if __name__ == "__main__":
    sys.exit(main())
