"""Compute a GraphQL query's node count from its text, before sending it.

Read only, and by default not even that: the node count is derived from the
query document, so the standard run makes no request, needs no token and spends
no points. The optional --confirm sends the document once and costs one point.

Queries only. GitHub's GraphQL endpoint takes a document in the request body, so
a read is carried by POST there just as a write would be; that is transport, not
intent. Any document containing a mutation or a subscription is refused before a
socket opens. Nothing is written and the repair is printed rather than performed.

GitHub caps one query at 500,000 nodes and computes the count from the first and
last values you requested, multiplied down through the nesting. Three levels of
first: 100 is 100 + 10,000 + 1,000,000 nodes. Because the cost comes from the
request rather than from what exists, a four-repository organisation fails
exactly as a four-thousand-repository one does.

What this can and cannot see: a first supplied as a variable cannot be evaluated
without the variables, and a named fragment spread hides part of the selection
set from a text-level analyser. Both are reported as caveats rather than folded
silently into the number.
"""
import argparse
import json
import logging
import os
import re
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_graphql_nodes")

API = "https://api.github.com"
UA = "github-graphql-nodes/1.0"

# The documented ceiling on one query. Named because it is printed and a reader
# checking this against the documentation should find it in one place.
NODE_LIMIT = 500_000

# Above this fraction of the cap, say so: a query at 98 per cent is one schema
# change away from being rejected.
NEAR = 0.8

# The canonical shape of the problem, used when no document is supplied.
DEMO_QUERY = """query {
  organization(login: "acme") {
    repositories(first: 100) {
      nodes {
        pullRequests(first: 100) {
          nodes {
            comments(first: 100) { nodes { id } }
          }
        }
      }
    }
  }
}"""

PAGING = re.compile(r"\b(first|last)\s*:\s*(\$?[A-Za-z0-9_]+)")
SPREAD = re.compile(r"\.\.\.\s*([A-Za-z_][A-Za-z0-9_]*)")


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


def commas(n):
    """Group a count in thousands so it can be read at a glance. Pure."""
    try:
        return "{:,}".format(int(n))
    except (TypeError, ValueError):
        return str(n)


def _paging(field, args, variables):
    """The slicing argument on one field, if there is one. Pure."""
    m = PAGING.search(args or "")
    if not m:
        return None
    arg, raw = m.group(1), m.group(2)
    variable = raw[1:] if raw.startswith("$") else None
    value = (variables or {}).get(variable) if variable else raw
    try:
        requested = int(value)
    except (TypeError, ValueError):
        requested = None
    return {"field": field or "?", "arg": arg, "variable": variable,
            "requested": requested}


def connections(document, variables=None):
    """Every sliced connection in a document, with its node contribution. Pure.

    Walks the text once with a stack of multipliers. A connection contributes
    the product of its own first/last and every one enclosing it, which is the
    rule the server applies and the reason a three-level query is a million
    nodes rather than three hundred.
    """
    src = strip_noise(document)
    out, stack, pending, field = [], [], None, None
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch == "(":
            j = src.find(")", i)
            args = src[i + 1:j] if j >= 0 else src[i + 1:]
            found = _paging(field, args, variables)
            # Only overwrite a pending slice when this argument group carries one,
            # so a directive such as @include(if: $x) cannot erase the first:
            # value that came immediately before it.
            if found is not None:
                pending = found
            i = n if j < 0 else j + 1
            continue
        if ch == "{":
            if pending is not None:
                ancestors = 1
                for m in stack:
                    ancestors *= m
                rec = dict(pending)
                rec["depth"] = len(stack) + 1
                rec["ancestors"] = ancestors
                rec["nodes"] = (None if rec["requested"] is None
                                else ancestors * rec["requested"])
                out.append(rec)
                stack.append(rec["requested"] if rec["requested"] else 1)
            else:
                stack.append(1)
            pending, field = None, None
            i += 1
            continue
        if ch == "}":
            if stack:
                stack.pop()
            pending, field = None, None
            i += 1
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            field = src[i:j]
            i = j
            continue
        i += 1
    return out


def node_count(document, variables=None):
    """The node total the server will compute for this document. Pure."""
    return sum(c["nodes"] for c in connections(document, variables)
               if c["nodes"] is not None)


def unresolved(document, variables=None):
    """Connections whose slice is a variable nobody supplied. Pure."""
    return [c["field"] for c in connections(document, variables)
            if c["requested"] is None]


def fragment_spreads(document):
    """Named fragment spreads, which hide part of the selection set. Pure."""
    src = strip_noise(document)
    return sorted({m.group(1) for m in SPREAD.finditer(src) if m.group(1) != "on"})


def caveats(document, variables=None):
    """Everything that makes the computed total less than certain. Pure."""
    out = []
    missing = unresolved(document, variables)
    if missing:
        out.append("the slice on %s is a variable this run has no value for, so "
                   "those connections are not in the total. Pass --variables."
                   % ", ".join(sorted(set(missing))))
    spreads = fragment_spreads(document)
    if spreads:
        out.append("the document spreads the fragment(s) %s, whose selection set "
                   "this text-level check does not expand, so the total is a "
                   "lower bound." % ", ".join(spreads))
    return out


def deepest(document, variables=None):
    """The connection carrying the largest multiplier. Pure. None if there is none."""
    resolved = [c for c in connections(document, variables) if c["nodes"] is not None]
    if not resolved:
        return None
    return max(resolved, key=lambda c: (c["depth"], c["nodes"]))


def reshape(document, variables=None, limit=NODE_LIMIT):
    """The largest slice the deepest connection could take. Pure.

    Returns (field, current, suggested). suggested is None when even a slice of
    one leaves the query over the cap, which means the shape itself has to
    change rather than a number in it.
    """
    d = deepest(document, variables)
    if d is None:
        return (None, None, None)
    total = node_count(document, variables)
    without = total - d["nodes"]
    room = limit - without
    if d["ancestors"] <= 0:
        return (d["field"], d["requested"], None)
    k = room // d["ancestors"]
    if k < 1:
        return (d["field"], d["requested"], None)
    return (d["field"], d["requested"], int(min(k, 100)))


def exceeds(count, limit=NODE_LIMIT):
    """Whether this node total is over the cap. Pure."""
    try:
        return int(count) > int(limit)
    except (TypeError, ValueError):
        return False


def verdict(document, variables=None, limit=NODE_LIMIT):
    """Classify one document. Pure. Returns (state, detail)."""
    conns = connections(document, variables)
    if not conns:
        return ("no-connections",
                "this document slices no connections, so it has no node count "
                "worth speaking of.")
    if any(c["nodes"] is None for c in conns):
        return ("unresolved-variables",
                "at least one slice is a variable with no value supplied, so the "
                "node count cannot be computed from the text alone.")
    total = node_count(document, variables)
    pct = round(100.0 * total / float(limit))
    if exceeds(total, limit):
        return ("over-node-limit",
                "%s nodes is %d%% of the %s cap, so this query is rejected before "
                "it runs whatever the organisation contains."
                % (commas(total), pct, commas(limit)))
    if total > limit * NEAR:
        return ("near-node-limit",
                "%s nodes is %d%% of the cap, which leaves no room for another "
                "level." % (commas(total), pct))
    return ("within-node-limit",
            "%s nodes is %d%% of the cap." % (commas(total), pct))


def repair(state, field=None, current=None, suggested=None):
    """The sentence a reader has to act on. Pure."""
    if state in ("over-node-limit", "near-node-limit"):
        if suggested is not None:
            return ("lower first on %s from %s to %d and paginate it separately "
                    "with pageInfo { hasNextPage endCursor }."
                    % (field, current, suggested))
        return ("even a slice of one on %s leaves this query over the cap, so "
                "split it into separate queries rather than tuning a number."
                % field)
    if state == "unresolved-variables":
        return ("pass the variables with --variables so the slices can be "
                "resolved. A slice you cannot evaluate is a slice you cannot "
                "budget for.")
    if state == "no-connections":
        return "nothing. There is nothing here to multiply."
    return "nothing on the node count."


def reported_node_count(body):
    """The node count the server computed, if the document asked for it. Pure."""
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    if not isinstance(data, dict):
        return None
    rl = data.get("rateLimit")
    if not isinstance(rl, dict):
        return None
    try:
        return int(rl.get("nodeCount"))
    except (TypeError, ValueError):
        return None


def rejected_for_nodes(body):
    """Whether the server refused this document for its size. Pure."""
    if not isinstance(body, dict):
        return False
    for err in body.get("errors") or []:
        if isinstance(err, dict) and err.get("type") == "MAX_NODE_LIMIT_EXCEEDED":
            return True
    return False


def point_cost(confirm):
    """Points this run will spend. Pure. Zero unless --confirm is passed."""
    return 1 if confirm else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", help="read the query document from this file")
    ap.add_argument("--query", help="the query document itself")
    ap.add_argument("--variables", help="JSON object supplying the query's variables")
    ap.add_argument("--confirm", action="store_true",
                    help="spend one point sending the document so the server can "
                         "agree or disagree")
    args = ap.parse_args()

    if args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            document = fh.read()
    else:
        document = args.query or DEMO_QUERY

    try:
        variables = json.loads(args.variables) if args.variables else {}
    except ValueError:
        log.error("--variables must be a JSON object")
        return 2

    why_not = refusal(document)
    if why_not:
        log.error("refusing to analyse and send: %s", why_not)
        return 2

    if args.confirm:
        log.info("point cost: %d point(s) against the 5,000/hour GraphQL budget",
                 point_cost(True))
    else:
        log.info("point cost: %d point(s). The node count is computed from the "
                 "query text and nothing is sent.", point_cost(False))

    conns = connections(document, variables)
    total = node_count(document, variables)
    log.info("node count: %s against a limit of %s", commas(total), commas(NODE_LIMIT))
    for c in conns:
        log.info("  %-16s %s=%-6s depth %-3d ancestors x%-8s %s nodes",
                 c["field"], c["arg"],
                 c["requested"] if c["requested"] is not None else "?",
                 c["depth"], commas(c["ancestors"]),
                 commas(c["nodes"]) if c["nodes"] is not None else "?")

    state, detail = verdict(document, variables)
    log.info("%s: %s", state, detail)
    for c in caveats(document, variables):
        log.info("caveat: %s", c)
    field, current, suggested = reshape(document, variables)
    log.info("repair: %s", repair(state, field, current, suggested))

    server = {}
    if args.confirm:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            log.error("--confirm needs GITHUB_TOKEN (a read-only token is enough)")
            return 2
        import requests
        session = requests.Session()
        session.headers.update({
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "User-Agent": UA,
        })
        # A GraphQL query is a read; POST is only how the document reaches the
        # endpoint, which is why the verb sits here beside the URL rather than
        # in a constant where it could be mistaken for a write path.
        resp = session.post(API + "/graphql",
                            json={"query": document, "variables": variables},
                            timeout=30)
        try:
            body = resp.json()
        except ValueError:
            body = None
        server = {"rejected": rejected_for_nodes(body),
                  "reported_node_count": reported_node_count(body)}
        if server["rejected"]:
            log.info("the server rejected the document for its node count, which "
                     "confirms the arithmetic above")
        elif server["reported_node_count"] is not None:
            log.info("the server computed %s node(s); this check computed %s",
                     commas(server["reported_node_count"]), commas(total))
        else:
            log.info("the server accepted the document and reported no node "
                     "count. Add rateLimit { nodeCount } to compare directly.")

    print(json.dumps({
        "points_spent": point_cost(args.confirm),
        "node_count": total,
        "node_limit": NODE_LIMIT,
        "over_limit": exceeds(total),
        "connections": conns,
        "caveats": caveats(document, variables),
        "deepest": deepest(document, variables),
        "suggested": {"field": field, "current": current, "first": suggested},
        "server": server,
        "state": state,
        "detail": detail,
    }, indent=2, default=str))
    return 1 if state in ("over-node-limit", "near-node-limit") else 0


if __name__ == "__main__":
    sys.exit(main())
