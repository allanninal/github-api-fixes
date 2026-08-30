"""Find nested GraphQL connections that truncated once per parent.

Read only, and queries only. GitHub's GraphQL endpoint takes a document in the
request body, so a read is carried by POST there just as a write would be; that
is transport, not intent. This script sends queries and refuses any document
containing a mutation or a subscription before it opens a socket. Nothing is
written and the repair is printed rather than performed.

Every connection carries its own cursor. Paginating the outer connection does
nothing for the inner ones: each new outer page restarts them from the start, so
each parent returns its first n children and stops, with no error and no marker.
The evidence is totalCount sitting next to a shorter list of nodes, once per
parent, and pageInfo.hasNextPage coming back true on a connection nobody
intended to follow.

What this can and cannot see: the API cannot tell whether your client follows a
cursor. What it can do is measure one response, report every connection that
returned fewer items than it holds, name the connections that asked for neither
totalCount nor pageInfo and therefore cannot be checked at all, and count the
follow-up queries that doing it properly would cost.

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
log = logging.getLogger("github_graphql_nested")

API = "https://api.github.com"
UA = "github-graphql-nested/1.0"

POINTS_PER_QUERY = 1

# Deliberately imperfect: the inner connection asks for totalCount but not for
# pageInfo, which is the common shape. It proves the truncation and gives you no
# cursor to continue from.
DEFAULT_QUERY = (
    "query($login: String!, $outer: Int = 5, $inner: Int = 5) {"
    " repositoryOwner(login: $login) {"
    " repositories(first: $outer, orderBy: {field: PUSHED_AT, direction: DESC}) {"
    " totalCount pageInfo { hasNextPage endCursor }"
    " nodes { name issues(first: $inner, states: OPEN) {"
    " totalCount nodes { number } } }"
    " } } }"
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


def outer_text(block):
    """A selection set with everything nested inside it blanked out. Pure.

    So that a pageInfo belonging to an inner connection is never credited to the
    connection that contains it, which is the mistake that makes a text-level
    audit of nested queries useless.
    """
    out, depth = [], 0
    for ch in str(block or ""):
        if ch == "{":
            depth += 1
            out.append(" ")
            continue
        if ch == "}":
            depth = max(0, depth - 1)
            out.append(" ")
            continue
        out.append(ch if depth == 0 else " ")
    return "".join(out)


def connection_fields(document, _depth=0, _stripped=False):
    """Every connection in the document, found by shape. Pure.

    A connection is a field whose own selection set contains nodes or edges.
    Identifying them that way rather than by their slicing argument means a
    connection paginated by a variable, or one written without arguments at all,
    is still found.
    """
    src = str(document or "") if _stripped else strip_noise(document)
    out, i, n, word, field = [], 0, len(src), "", ""
    while i < n:
        ch = src[i]
        if ch.isalnum() or ch == "_":
            word += ch
            i += 1
            continue
        # The field name has to survive the whitespace and the argument list
        # between it and its selection set, so it is remembered rather than
        # read off whatever happens to precede the brace.
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
            i = j + 1
            continue
        if ch == "{":
            j, level = i, 0
            while j < n:
                if src[j] == "{":
                    level += 1
                elif src[j] == "}":
                    level -= 1
                    if level == 0:
                        break
                j += 1
            block = src[i + 1:j]
            own = outer_text(block).split()
            if field and ("nodes" in own or "edges" in own):
                out.append({"field": field, "depth": _depth,
                            "has_page_info": "pageInfo" in own,
                            "has_total_count": "totalCount" in own})
                out.extend(connection_fields(block, _depth + 1, True))
            else:
                out.extend(connection_fields(block, _depth, True))
            i, field = j + 1, ""
            continue
        if ch == "}":
            field = ""
        i += 1
    return out


def unauditable(fields):
    """Inner connections that asked for neither totalCount nor pageInfo. Pure."""
    return [f for f in fields
            if f["depth"] >= 1 and not f["has_total_count"] and not f["has_page_info"]]


def unresumable(fields):
    """Inner connections that can be seen to truncate but not continued. Pure."""
    return [f for f in fields
            if f["depth"] >= 1 and f["has_total_count"] and not f["has_page_info"]]


def is_connection(value):
    """Whether a decoded object is a connection. Pure."""
    if not isinstance(value, dict):
        return False
    return isinstance(value.get("nodes"), list) or isinstance(value.get("edges"), list)


def walk_connections(data, path="", depth=0):
    """Every connection in a decoded response, with its path and depth. Pure.

    Depth counts connections above this one rather than keys, so an inner
    connection is depth 1 however many plain objects sit between it and its
    parent connection.
    """
    out = []
    if isinstance(data, dict):
        if is_connection(data):
            items = data.get("nodes")
            if not isinstance(items, list):
                items = data.get("edges") or []
            page = data.get("pageInfo")
            total = data.get("totalCount")
            out.append({
                "path": path or "(root)",
                "depth": depth,
                "returned": len(items),
                "total_count": total if isinstance(total, int) else None,
                "has_next_page": page.get("hasNextPage") if isinstance(page, dict) else None,
                "end_cursor": page.get("endCursor") if isinstance(page, dict) else None,
            })
            depth += 1
        for key, value in data.items():
            out.extend(walk_connections(value, key if not path else path + "." + key, depth))
    elif isinstance(data, list):
        for index, item in enumerate(data):
            out.extend(walk_connections(item, "%s[%d]" % (path, index), depth))
    return out


def missing(entry):
    """Items this connection holds and did not return, or None. Pure."""
    total = entry.get("total_count")
    if not isinstance(total, int):
        return None
    return max(0, total - entry.get("returned", 0))


def truncated(entry):
    """Whether this connection stopped short of what it holds. Pure."""
    if entry.get("has_next_page") is True:
        return True
    gap = missing(entry)
    return bool(gap)


def auditable(entry):
    """Whether the response says anything at all about completeness. Pure."""
    return entry.get("total_count") is not None or entry.get("has_next_page") is not None


def resumable(entry):
    """Whether this connection can be continued without refetching its parent."""
    return bool(entry.get("end_cursor")) or entry.get("has_next_page") is not None


def followup_queries(entries):
    """Queries a correct inner walk would cost, from what this response shows.

    One per truncated parent at minimum, and more where the gap is wider than a
    single page of the size that was requested. Pure.
    """
    total = 0
    for entry in entries or []:
        if entry.get("depth", 0) < 1 or not truncated(entry):
            continue
        gap = missing(entry)
        page = entry.get("returned") or 0
        if gap and page > 0:
            total += -(-gap // page)
        else:
            total += 1
    return total


def classify(entries):
    """Classify one response. Pure. Returns (state, detail)."""
    if not entries:
        return ("no-connection-in-the-response",
                "nothing in this response has nodes or edges, so there is no "
                "connection here to be truncated.")
    inner = [e for e in entries if e["depth"] >= 1]
    inner_cut = [e for e in inner if truncated(e)]
    if inner_cut:
        gaps = [missing(e) for e in inner_cut if missing(e) is not None]
        return ("inner-connection-truncated",
                "%d of %d inner connection(s) returned fewer items than they "
                "contain and %s item(s) are missing with no error raised."
                % (len(inner_cut), len(inner),
                   sum(gaps) if gaps else "an unknown number of"))
    blind = [e for e in inner if not auditable(e)]
    if blind:
        return ("inner-connection-unauditable",
                "%d of %d inner connection(s) asked for neither totalCount nor "
                "pageInfo, so this response cannot say whether they truncated."
                % (len(blind), len(inner)))
    outer_cut = [e for e in entries if e["depth"] == 0 and truncated(e)]
    if outer_cut:
        return ("outer-connection-truncated",
                "the outer connection has more pages and every inner connection "
                "in it is complete. This is the truncation people do notice.")
    return ("complete",
            "every connection in this response returned everything it holds, so "
            "a total computed over it really is a total.")


def repair(state):
    """The sentence a reader has to act on. Pure."""
    if state == "inner-connection-truncated":
        return ("add pageInfo { hasNextPage endCursor } to every nested "
                "connection and walk each truncated parent separately with "
                "after: endCursor. An outer loop cannot do this for you.")
    if state == "inner-connection-unauditable":
        return ("add totalCount and pageInfo { hasNextPage endCursor } to the "
                "nested connections first. They cost nothing and without them "
                "nobody can tell whether this response is complete.")
    if state == "outer-connection-truncated":
        return ("follow the outer cursor as you already do, and keep checking "
                "the inner connections on every page: they restart from the "
                "beginning each time the outer one advances.")
    if state == "complete":
        return ("nothing here. Re-run it against a parent that really has more "
                "than one page of children, since a connection that fits cannot "
                "demonstrate a connection that does not.")
    return "point the query at something with a connection in it."


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
    ap.add_argument("--login", help="user or organisation to probe with the "
                                    "default query")
    ap.add_argument("--outer", type=int, default=5, help="outer page size")
    ap.add_argument("--inner", type=int, default=5, help="inner page size")
    ap.add_argument("--file", help="a .graphql file to send instead")
    ap.add_argument("--query", help="the document as a string")
    ap.add_argument("--variables", default="{}", help="JSON object of variables")
    args = ap.parse_args()

    if args.file:
        document = Path(args.file).read_text(encoding="utf-8")
    else:
        document = args.query or DEFAULT_QUERY

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
        variables.update({"login": args.login, "outer": args.outer,
                          "inner": args.inner})

    why_not = refusal(document)
    if why_not:
        log.error("refusing to send: %s", why_not)
        return 2

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    log.info("point cost: %d point(s) against the 5,000/hour GraphQL budget",
             point_cost(1))

    fields = connection_fields(document)
    for f in unauditable(fields):
        log.info("document: %s asks for neither totalCount nor pageInfo, so no "
                 "response can say whether it truncated", f["field"])
    for f in unresumable(fields):
        log.info("document: %s asks for totalCount but not pageInfo, so "
                 "truncation is visible and cannot be resumed without "
                 "refetching the parent", f["field"])

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })
    status, body = run_query(session, document, variables)
    if not isinstance(body, dict):
        log.error("HTTP %s and no JSON body to read", status)
        return 2
    if body.get("errors"):
        log.error("the query itself failed: %s",
                  json.dumps(body["errors"])[:400])
        return 2

    entries = walk_connections(body.get("data") or {})
    for e in entries:
        gap = missing(e)
        note = "complete"
        if truncated(e):
            note = ("%s missing" % gap) if gap is not None else "more pages"
            if not resumable(e):
                note += ", no cursor"
        log.info("  %-46s depth %d  %d of %s  %s",
                 e["path"], e["depth"], e["returned"],
                 "?" if e["total_count"] is None else e["total_count"], note)

    state, detail = classify(entries)
    log.info("%s: %s", state, detail)
    follow = followup_queries(entries)
    if follow:
        log.info("following them properly costs %d more quer%s, at least one "
                 "per truncated parent", follow, "y" if follow == 1 else "ies")
    log.info("repair: %s", repair(state))

    print(json.dumps({"points_spent": point_cost(1), "state": state,
                      "followup_queries": follow, "connections": entries,
                      "document": fields}, indent=2, default=str))
    return 1 if state in ("inner-connection-truncated",
                          "inner-connection-unauditable") else 0


if __name__ == "__main__":
    sys.exit(main())
