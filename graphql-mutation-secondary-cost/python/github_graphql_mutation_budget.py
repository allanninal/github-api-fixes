"""Price GraphQL documents against the per-minute secondary limit.

Read only, and queries only. GitHub's GraphQL endpoint takes its document in
the request body, so a read travels by POST there exactly as a write would;
that is a transport detail, not a licence to write. This script parses every
document it is given, refuses to open a socket for anything containing a
mutation or a subscription, and sends exactly one read query of its own.

The point of the note: against the secondary rate limit of 2,000 points per
minute, a GraphQL request whose document contains a mutation counts as 5
points and one that does not counts as 1. So a write loop reaches the limit at
roughly a fifth of the request rate a read loop survives, and it does so with
the separate hourly point budget almost untouched.

What this can and cannot see: secondary limits have no bucket. GET /rate_limit
reports the hourly budget only, and nothing anywhere reports how close you are
to the per-minute ceiling. So the ceiling is computed from the documented
weights and compared against a rate you supply, and a throttle you already
recorded is classified after the fact.

Environment:

    GITHUB_TOKEN    a token with read access to the GraphQL API
"""
import argparse
import json
import logging
import math
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_graphql_mutation_budget")

API = "https://api.github.com"
UA = "github-graphql-mutation-budget/1.0"

# The secondary limit on the GraphQL endpoint, and the two weights it applies.
# Documented, not measurable: no response reports headroom against this.
SECONDARY_POINTS_PER_MINUTE = 2000
WEIGHT_WITH_MUTATION = 5
WEIGHT_WITHOUT_MUTATION = 1

# The other bucket entirely, quoted here only so the two are never confused.
PRIMARY_POINTS_PER_HOUR = 5000

# GitHub asks for at least this long between mutations affecting one resource.
# Not expressed in points, so satisfying the arithmetic does not satisfy it.
SAME_RESOURCE_GAP_SECONDS = 1.0

# The one document this script ever sends. A read, and it is put through the
# same refusal check as anything supplied on the command line.
PROBE_QUERY = "query { rateLimit { limit cost remaining used resetAt } }"

# This run's own cost against the hourly budget.
POINTS_PER_QUERY = 1


def strip_noise(document):
    """Remove GraphQL comments and string literals from a document. Pure.

    A scanner rather than a regex: a hash inside a string literal is an
    ordinary character and a comment marker outside one, and the word
    "mutation" inside a search string is not a mutation. Getting that wrong
    here would misprice a document as well as misjudge whether to send it.
    """
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
    """The top-level operations in a document, in order. Pure.

    One entry per brace group at depth zero: "query", "mutation",
    "subscription" or "fragment". An anonymous document is query shorthand.
    """
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
    """Why this document will not be sent, or None if it is a read. Pure.

    The endpoint is the same one mutations go to, so the guard lives in code
    rather than in a comment. This script has an opinion about mutations and
    still never transmits one.
    """
    ops = operations(document)
    if not ops:
        return "the document contains no operation to send."
    for kind in ("mutation", "subscription"):
        if kind in ops:
            return ("the document contains a %s. This script prices documents, "
                    "it does not send them: a query is a read, and the section "
                    "it belongs to promises its scripts never write." % kind)
    return None


def weight(document):
    """Secondary-limit points for one request carrying this document. Pure.

    Per request, not per mutation: a document with six mutations in it is
    priced at 5, the same as a document with one. That is why batching is a
    real reduction and splitting is a fivefold increase.
    """
    return WEIGHT_WITH_MUTATION if "mutation" in operations(document) else WEIGHT_WITHOUT_MUTATION


def ceiling_per_minute(points):
    """Requests a minute this weight allows before the limit binds. Pure."""
    if not points or points <= 0:
        return 0
    return SECONDARY_POINTS_PER_MINUTE // int(points)


def min_gap_seconds(points):
    """Seconds between requests implied by the ceiling, one worker. Pure."""
    ceiling = ceiling_per_minute(points)
    return 0.0 if ceiling <= 0 else 60.0 / ceiling


def points_per_minute(rate, points):
    """What a given request rate costs against the per-minute limit. Pure."""
    return max(0, int(rate or 0)) * int(points or 0)


def minutes_for_batch(count, rate):
    """How long a batch of this size takes at this rate, in minutes. Pure."""
    rate = max(0, int(rate or 0))
    if rate <= 0:
        return None
    return math.ceil(max(0, int(count or 0)) / rate)


def classify_rate(rate, points):
    """Judge a request rate against the per-minute limit. Pure.

    Returns (state, detail). The middle state matters: a rate can be legal on
    points and still wrong, because concurrency and the same-resource gap are
    separate rules that points do not express.
    """
    spend = points_per_minute(rate, points)
    ceiling = ceiling_per_minute(points)
    if not rate:
        return ("not-measured",
                "no rate given, so this document is priced but not judged. "
                "Its ceiling is %d request(s)/minute." % ceiling)
    if spend > SECONDARY_POINTS_PER_MINUTE:
        return ("over-ceiling",
                "%d request(s)/minute of this document is %d point(s)/minute "
                "against a limit of %d." % (rate, spend, SECONDARY_POINTS_PER_MINUTE))
    if spend > SECONDARY_POINTS_PER_MINUTE * 0.8:
        return ("near-ceiling",
                "%d request(s)/minute is %d point(s)/minute, inside the limit of "
                "%d but with under a fifth of it left."
                % (rate, spend, SECONDARY_POINTS_PER_MINUTE))
    return ("within-ceiling",
            "%d request(s)/minute is %d point(s)/minute against a limit of %d."
            % (rate, spend, SECONDARY_POINTS_PER_MINUTE))


def classify_throttle(status, message, graphql_remaining):
    """Attribute a recorded failure to one bucket or the other. Pure.

    Returns (state, detail). The whole diagnosis is two readings taken
    together: which failure arrived, and what the hourly budget said at that
    moment. Either on its own is ambiguous.
    """
    text = str(message or "").lower()
    secondary = "secondary rate limit" in text
    try:
        remaining = int(graphql_remaining)
    except (TypeError, ValueError):
        remaining = None
    healthy = remaining is not None and remaining > PRIMARY_POINTS_PER_HOUR * 0.1

    if secondary and healthy:
        return ("secondary-not-budget",
                "a secondary rate limit with %d point(s) still in the hourly "
                "budget. This is the per-minute ceiling, and no amount of "
                "waiting for the hourly reset will help." % remaining)
    if secondary:
        return ("secondary-limit",
                "a secondary rate limit. The hourly budget was not readable or "
                "was itself low, so slow down and check both.")
    if "rate limit" in text and remaining == 0:
        return ("primary-exhausted",
                "the hourly point budget is spent. That is a different bucket "
                "with a different note and it refills on a schedule.")
    if "rate limit" in text:
        return ("rate-limited-unclassified",
                "a rate-limit message that does not name the secondary limit. "
                "Read resources.graphql at the moment of failure to attribute it.")
    if str(status) in ("403", "429"):
        return ("forbidden-not-throttled",
                "HTTP %s with no rate-limit wording, so this is a permission "
                "problem rather than a throttle." % status)
    return ("no-throttle", "nothing in this record names a rate limit.")


def repair(state):
    """The sentence a reader has to act on. Pure."""
    if state == "over-ceiling":
        return ("batch mutations into one document, serialise the loop, and cap "
                "it at %d/minute or below. The 5 points are charged per "
                "request, so fewer requests is the whole lever."
                % ceiling_per_minute(WEIGHT_WITH_MUTATION))
    if state == "near-ceiling":
        return ("leave headroom. A retry, a redeploy or one extra worker puts "
                "this over, and the limit gives no warning before it binds.")
    if state == "secondary-not-budget":
        return ("rate-limit the writer against points a minute, not requests a "
                "minute, and honour retry-after. Do not rewrite the retry "
                "logic around the hourly budget; that bucket was fine.")
    if state == "secondary-limit":
        return ("slow the writer down and record resources.graphql at the "
                "moment of failure so the next one can be attributed.")
    if state == "primary-exhausted":
        return ("see /github/graphql-rate-limited/ -- the hourly point budget "
                "is a different bucket and this is not the note for it.")
    if state == "within-ceiling":
        return ("nothing on the point arithmetic. Check concurrency and the "
                "one-second gap between mutations on the same resource "
                "separately; points do not express either.")
    return ("supply the rate the loop actually runs at, or the failure you "
            "recorded, and the arithmetic becomes a verdict.")


def price(label, document, rate):
    """Everything this script knows about one document. Pure."""
    ops = operations(document)
    points = weight(document)
    state, detail = classify_rate(rate, points)
    return {
        "document": label,
        "operations": ops,
        "points_per_request": points,
        "ceiling_per_minute": ceiling_per_minute(points),
        "min_gap_seconds": round(min_gap_seconds(points), 4),
        "points_per_minute_at_rate": points_per_minute(rate, points),
        "not_sent": refusal(document),
        "state": state,
        "detail": detail,
        "repair": repair(state),
    }


def run_query(session, document, variables=None):
    """Send one read query. Returns (status, body-or-None).

    A GraphQL query is a read; POST is only how the document reaches the
    endpoint, which is why the verb is written here beside the URL rather than
    hidden in a constant where it could be mistaken for a write path.
    """
    r = session.post(API + "/graphql",
                     json={"query": document, "variables": variables or {}},
                     timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed "
                         "or revoked")
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


def graphql_budget(session):
    """The hourly GraphQL bucket, read for free. Returns a dict or None."""
    r = session.get(API + "/rate_limit", timeout=30)
    if r.status_code != 200:
        return None
    try:
        return (r.json().get("resources") or {}).get("graphql")
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--document", action="append", default=[],
                    help="path to a .graphql file to price. Repeatable. "
                         "Mutation documents are priced and never sent.")
    ap.add_argument("--query",
                    help="price a document given inline instead of from a file")
    ap.add_argument("--rate", type=int, default=0,
                    help="requests a minute the loop actually sends")
    ap.add_argument("--batch", type=int, default=0,
                    help="how many rows the job has to get through")
    ap.add_argument("--throttle-message", default="",
                    help="the error body you recorded, to attribute it")
    ap.add_argument("--throttle-status", default="",
                    help="the status code you recorded alongside it")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    documents = []
    for path in args.document:
        try:
            documents.append((path, open(path, encoding="utf-8").read()))
        except OSError as exc:
            log.error("cannot read %s: %s", path, exc)
            return 2
    if args.query:
        documents.append(("--query", args.query))
    if not documents:
        log.error("give at least one --document or --query to price")
        return 2

    log.info("point cost: %d point(s) against the %d/hour GraphQL budget",
             POINTS_PER_QUERY, PRIMARY_POINTS_PER_HOUR)

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })

    budget = graphql_budget(session)
    if budget:
        log.info("graphql budget: %s/%s remaining", budget.get("remaining"),
                 budget.get("limit"))
    # The probe goes through the same guard as anything supplied by the caller.
    if refusal(PROBE_QUERY) is None:
        status, body = run_query(session, PROBE_QUERY)
        log.info("probe read query: HTTP %s, %d point(s) spent", status,
                 POINTS_PER_QUERY)
        if isinstance(body, dict) and body.get("errors"):
            log.warning("the probe itself carried errors: %s",
                        json.dumps(body["errors"])[:200])

    priced = [price(label, doc, args.rate) for label, doc in documents]
    for p in priced:
        log.info("%s: operations=%s -> %d point(s) per request",
                 p["document"], ", ".join(p["operations"]) or "none",
                 p["points_per_request"])
        if p["not_sent"]:
            log.info("  not sent: %s", p["not_sent"])
        log.info("  ceiling %d request(s)/minute, minimum gap %.3fs on one worker",
                 p["ceiling_per_minute"], p["min_gap_seconds"])
        log.info("  %s: %s", p["state"], p["detail"])
        if args.batch:
            at_ceiling = minutes_for_batch(args.batch, p["ceiling_per_minute"])
            log.info("  %d row(s) takes at least %s minute(s) at the ceiling",
                     args.batch, at_ceiling)
        log.info("  repair: %s", p["repair"])

    throttle = None
    if args.throttle_message or args.throttle_status:
        state, detail = classify_throttle(
            args.throttle_status, args.throttle_message,
            (budget or {}).get("remaining"))
        log.info("recorded failure -> %s: %s", state, detail)
        log.info("repair: %s", repair(state))
        throttle = {"state": state, "detail": detail}

    print(json.dumps({
        "points_spent": POINTS_PER_QUERY,
        "secondary_points_per_minute": SECONDARY_POINTS_PER_MINUTE,
        "same_resource_gap_seconds": SAME_RESOURCE_GAP_SECONDS,
        "graphql_budget": budget,
        "documents": priced,
        "recorded_failure": throttle,
    }, indent=2, default=str))
    over = [p for p in priced if p["state"] in ("over-ceiling", "near-ceiling")]
    return 1 if over or (throttle and throttle["state"].startswith("secondary")) else 0


if __name__ == "__main__":
    sys.exit(main())
