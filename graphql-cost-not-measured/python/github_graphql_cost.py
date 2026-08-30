"""Measure what a GraphQL query costs and compare it with what anybody assumed.

Read only, and queries only. GitHub's GraphQL endpoint takes a document in the
request body, so a read is carried by POST there just as a write would be; that
is transport, not intent. This script sends queries and refuses any document
containing a mutation or a subscription before it opens a socket. Nothing is
written -- including the baseline file, which is printed for you to update
rather than rewritten here.

A query's point cost is computed from the connections it could traverse and the
slice each one asked for, not from the data that came back, so a query returning
a dozen rows can cost more than a dozen points. The server reports the number in
band if you ask for it, and there is no server-side history of what your queries
cost, so a price nobody recorded is a price nobody can compare against.

What this can and cannot see: the measurement is authoritative for the document
and variables handed to it. It cannot see the other shapes your integration
sends, and it cannot attribute the budget's drain to any of them, because the
bucket is shared by every process holding the token.

Environment:

    GITHUB_TOKEN    a token with read access to the GraphQL API
"""
import argparse
import json
import logging
import os
import sys
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_graphql_cost")

API = "https://api.github.com"
UA = "github-graphql-cost/1.0"

POINTS_PER_QUERY = 1

# The selection that makes a response report its own price. Cheap to carry: it
# adds no round trip and the response is one object longer.
RATE_LIMIT_SELECTION = "rateLimit { cost nodeCount limit remaining resetAt }"

DEFAULT_QUERY = (
    "query($login: String!) {"
    " repositoryOwner(login: $login) {"
    " repositories(first: 50) { totalCount nodes { name"
    " issues(first: 20, states: OPEN) { totalCount nodes { number } } } }"
    " } }"
)


def blank_noise(document):
    """Comments and string literals replaced by spaces. Pure.

    Length preserving, unlike a scanner that removes them, because this one is
    used to compute an index back into the original text.
    """
    src = str(document or "")
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch == "#":
            while i < n and src[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if src.startswith('"""', i):
            j = src.find('"""', i + 3)
            end = n if j < 0 else j + 3
            for k in range(i, end):
                out[k] = " "
            i = end
            continue
        if ch == '"':
            out[i] = " "
            i += 1
            while i < n and src[i] != '"':
                step = 2 if src[i] == "\\" else 1
                for k in range(i, min(n, i + step)):
                    out[k] = " "
                i += step
            if i < n:
                out[i] = " "
            i += 1
            continue
        i += 1
    return "".join(out)


def operations(document):
    """The top-level operations in a document, in order. Pure."""
    src = blank_noise(document)
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


def selection_set_start(document):
    """Index of the operation's opening brace, or -1. Pure.

    Braces inside the variable definitions -- an input object used as a default
    value -- are skipped, because inserting a selection there would produce a
    document that no longer parses.
    """
    src = blank_noise(document)
    parens = 0
    for i, ch in enumerate(src):
        if ch == "(":
            parens += 1
        elif ch == ")":
            parens = max(0, parens - 1)
        elif ch == "{" and parens == 0:
            return i
    return -1


def inject_rate_limit(document):
    """The document with rateLimit added to its top-level selection. Pure.

    Idempotent: a document that already asks for it comes back untouched, so
    running this over a repository of queries does not accumulate duplicates.
    """
    src = str(document or "")
    if "rateLimit" in blank_noise(src):
        return src
    at = selection_set_start(src)
    if at < 0:
        return src
    return src[:at + 1] + " " + RATE_LIMIT_SELECTION + src[at + 1:]


def slicing_pairs(argument_text):
    """The first and last arguments in one argument list. Pure.

    Splits on top-level commas so a nested input object does not confuse the
    scan, and matches the key exactly so a variable definition reading
    `$first: Int = 250` is never counted as an argument called first.
    """
    src = str(argument_text or "")
    parts, depth, cur = [], 0, ""
    for ch in src:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
            continue
        cur += ch
    parts.append(cur)
    out = []
    for part in parts:
        key, sep, value = part.partition(":")
        if sep and key.strip() in ("first", "last"):
            out.append((key.strip(), value.strip()))
    return out


def variable_defaults(document):
    """Defaults declared in the operation's variable definitions. Pure."""
    head = blank_noise(document).split("{", 1)[0]
    out = {}
    for part in head.replace("(", " ").replace(")", " ").split(","):
        name, sep, rest = part.partition(":")
        if not sep:
            continue
        name = (name.strip().rsplit(None, 1) or [""])[-1]
        if not name.startswith("$") or "=" not in rest:
            continue
        out[name] = rest.split("=", 1)[1].strip()
    return out


def as_int(value):
    """An integer, or None if this is not one. Pure."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def resolve_slice(raw, defaults=None, variables=None):
    """One written slicing value resolved to (value, source). Pure."""
    text = str(raw or "").strip()
    if not text:
        return (None, "missing")
    if not text.startswith("$"):
        return (as_int(text), "literal")
    supplied = variables if isinstance(variables, dict) else {}
    if text[1:] in supplied:
        return (as_int(supplied[text[1:]]), "variable-supplied")
    if text in (defaults or {}):
        return (as_int((defaults or {})[text]), "variable-default")
    return (None, "unresolved")


def slice_values(document, variables=None):
    """Every first and last in the document, resolved. Pure."""
    src = blank_noise(document)
    defaults = variable_defaults(document)
    out, i, n, word, field = [], 0, len(src), "", ""
    while i < n:
        ch = src[i]
        if ch.isalnum() or ch == "_":
            word += ch
            i += 1
            continue
        if word:
            field, word = word, ""
        if ch == "(":
            j, level = i, 0
            while j < n:
                if src[j] == "(":
                    level += 1
                elif src[j] == ")":
                    level -= 1
                    if level == 0:
                        break
                j += 1
            for arg, raw in slicing_pairs(src[i + 1:j]):
                value, source = resolve_slice(raw, defaults, variables)
                out.append({"field": field, "arg": arg, "written": raw,
                            "value": value, "source": source})
            i = j + 1
            continue
        i += 1
    return out


def predicted_cost(document, variables=None):
    """The documented approximation, from the text. Pure.

    Returns (points, unresolved). Roughly the sum of the slices divided by 100
    with a minimum of one, which is a prediction rather than an answer: its job
    is to be something the server's number can disagree with.
    """
    values = slice_values(document, variables)
    total, unresolved = 0, 0
    for v in values:
        if isinstance(v["value"], int) and v["value"] > 0:
            total += v["value"]
        else:
            unresolved += 1
    return (max(1, -(-total // 100)), unresolved)


def find_rate_limit(body):
    """The rateLimit object anywhere in a response. Pure."""
    if isinstance(body, dict):
        node = body.get("rateLimit")
        if isinstance(node, dict):
            return node
        for value in body.values():
            found = find_rate_limit(value)
            if found is not None:
                return found
    elif isinstance(body, list):
        for item in body:
            found = find_rate_limit(item)
            if found is not None:
                return found
    return None


def measured_cost(body):
    """What the server charged for this call, or None. Pure."""
    node = find_rate_limit(body) or {}
    cost = node.get("cost")
    return cost if isinstance(cost, int) else None


def measured_nodes(body):
    """The node count the server computed for this call, or None. Pure."""
    node = find_rate_limit(body) or {}
    count = node.get("nodeCount")
    return count if isinstance(count, int) else None


def returned_nodes(body):
    """How many items actually came back in every nodes list. Pure."""
    total = 0
    if isinstance(body, dict):
        for key, value in body.items():
            if key in ("nodes", "edges") and isinstance(value, list):
                total += len(value)
            total += returned_nodes(value)
    elif isinstance(body, list):
        for item in body:
            total += returned_nodes(item)
    return total


def gap(predicted, measured):
    """The disagreement between the text and the server. Pure."""
    if measured is None:
        return (None, "unmeasured")
    if not predicted or predicted <= 0:
        return (None, "unpredictable")
    ratio = measured / float(predicted)
    if ratio >= 2:
        return (ratio, "far-above-the-text")
    if ratio > 1.25:
        return (ratio, "above-the-text")
    if ratio < 0.75:
        return (ratio, "below-the-text")
    return (ratio, "close-to-the-text")


def drift(baseline, measured):
    """This shape's price against the recorded one. Pure."""
    if not isinstance(baseline, int):
        return ("no-baseline",
                "no recorded cost for this shape, so nothing can be compared. "
                "Record this one and the next change becomes visible.")
    if measured is None:
        return ("unmeasured", "nothing to compare the baseline against.")
    if measured == baseline:
        return ("unchanged",
                "this shape costs the same %d point(s) it did when the baseline "
                "was written." % baseline)
    direction = "rise" if measured > baseline else "fall"
    percent = abs(measured - baseline) * 100.0 / max(1, baseline)
    return ("increased" if measured > baseline else "decreased",
            "this shape cost %d point(s) when the baseline was written and costs "
            "%d now, a %s of %.0f%%." % (baseline, measured, direction, percent))


def classify(measured, predicted, baseline=None, returned=None):
    """Classify one measurement. Pure. Returns (state, detail)."""
    if measured is None:
        return ("cost-unmeasured",
                "the response carried no rateLimit { cost }, so this run "
                "measured nothing. Nothing else here is worth reading.")
    drift_state, drift_detail = drift(baseline, measured)
    if drift_state == "increased":
        return ("cost-increased-since-the-baseline", drift_detail)
    ratio, verdict = gap(predicted, measured)
    if verdict in ("far-above-the-text", "above-the-text"):
        return ("cost-above-the-shape-of-the-query",
                "the server charged %d where the document predicted %d, a "
                "factor of %.1f." % (measured, predicted, ratio))
    if isinstance(returned, int) and measured >= 5 and returned <= measured:
        return ("cost-unrelated-to-the-data-returned",
                "%d node(s) came back for %d point(s). The price follows what "
                "the query asked for, not what it found."
                % (returned, measured))
    return ("cost-measured",
            "this shape costs %d point(s), which is what the document predicts "
            "and what the baseline says." % measured)


def repair(state):
    """The sentence a reader has to act on. Pure."""
    if state == "cost-increased-since-the-baseline":
        return ("record the new cost against the shape and treat the change as "
                "part of the diff that caused it. A price change belongs in a "
                "code review, not in an incident.")
    if state == "cost-above-the-shape-of-the-query":
        return ("find what the document traverses that the arithmetic did not "
                "see -- usually a connection nested inside another -- and split "
                "the query rather than widening the budget.")
    if state == "cost-unrelated-to-the-data-returned":
        return ("lower the first values rather than filtering harder. Filters "
                "change what comes back; only the slice changes the price.")
    if state == "cost-unmeasured":
        return ("add rateLimit { cost nodeCount remaining } to the query. It "
                "costs no extra round trip and there is no other way to learn "
                "the number.")
    if state == "cost-measured":
        return ("record this number so the next change to the query has "
                "something to be compared against.")
    return "point the check at a document this endpoint can answer."


def points_per_hour(cost, calls_per_hour):
    """What a schedule spends. Pure."""
    if not isinstance(cost, int) or not calls_per_hour:
        return None
    return cost * int(calls_per_hour)


def point_cost(queries):
    """Points this run will spend. Pure."""
    return int(queries or 0) * POINTS_PER_QUERY


def run_query(session, document, variables):
    """Send one query. Returns (status, body-or-None).

    A GraphQL query is a read; POST is only how the document reaches the
    endpoint, which is why the verb is written here beside the URL rather than
    tucked into a constant where it could be mistaken for a write path.
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


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", help="a .graphql file to price")
    ap.add_argument("--query", help="the document as a string")
    ap.add_argument("--variables", default="{}", help="JSON object of variables")
    ap.add_argument("--login", help="user or organisation for the default query")
    ap.add_argument("--name", default="query", help="name for this shape in the baseline")
    ap.add_argument("--assumed", type=int, help="the cost somebody believes this has")
    ap.add_argument("--baseline", help="JSON file of recorded costs per shape name")
    ap.add_argument("--calls-per-hour", type=int, default=0,
                    help="how often this shape is sent, for an hourly projection")
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

    baseline = None
    if args.baseline:
        recorded = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        value = recorded.get(args.name) if isinstance(recorded, dict) else None
        baseline = value if isinstance(value, int) else None

    log.info("point cost: %d point(s) against the 5,000/hour GraphQL budget",
             point_cost(1))
    predicted, unresolved = predicted_cost(document, variables)
    slices = slice_values(document, variables)
    log.info("predicted from the text: %d point(s) from %d slicing argument(s) "
             "totalling %d", predicted, len(slices),
             sum(v["value"] for v in slices if isinstance(v["value"], int)))
    if unresolved:
        log.info("%d slicing argument(s) could not be resolved, so the "
                 "prediction is a lower bound", unresolved)

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })
    status, body = run_query(session, inject_rate_limit(document), variables)
    if isinstance(body, dict) and body.get("errors"):
        log.error("the query itself failed: %s", json.dumps(body["errors"])[:400])
        return 2

    measured = measured_cost(body)
    nodes = measured_nodes(body)
    returned = returned_nodes(body.get("data") if isinstance(body, dict) else None)
    log.info("measured by the server: %s point(s), nodeCount %s",
             "?" if measured is None else measured, "?" if nodes is None else nodes)
    if args.assumed is not None:
        log.info("assumed by the caller: %d point(s)", args.assumed)
    if baseline is not None:
        log.info("recorded baseline: %d point(s)", baseline)

    state, detail = classify(measured, predicted, baseline, returned)
    log.info("%s: %s", state, detail)
    if measured is not None:
        log.info("%d node(s) came back for %d point(s), so the price is not the "
                 "size of the answer", returned, measured)
        if args.assumed is not None and args.assumed != measured:
            log.info("the assumption is out by a factor of %.1f, and every "
                     "capacity number built on it is out by the same factor",
                     measured / float(max(1, args.assumed)))
    projected = points_per_hour(measured, args.calls_per_hour)
    if projected:
        log.info("at %d call(s)/hour this shape needs %d points/hour. What that "
                 "means against your quota is /github/graphql-rate-limited/",
                 args.calls_per_hour, projected)
    log.info("repair: %s", repair(state))
    if measured is not None:
        log.info('record it: "%s": %d', args.name, measured)

    print(json.dumps({"points_spent": point_cost(1), "state": state,
                      "predicted": predicted, "measured": measured,
                      "assumed": args.assumed, "baseline": baseline,
                      "node_count": nodes, "returned_nodes": returned,
                      "points_per_hour": projected, "slices": slices},
                     indent=2, default=str))
    return 1 if state in ("cost-increased-since-the-baseline",
                          "cost-above-the-shape-of-the-query",
                          "cost-unrelated-to-the-data-returned") else 0


if __name__ == "__main__":
    sys.exit(main())
