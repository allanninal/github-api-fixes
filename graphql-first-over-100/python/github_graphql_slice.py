"""Resolve every first and last in a GraphQL document against the ceiling of 100.

Read only, and queries only. GitHub's GraphQL endpoint takes a document in the
request body, so a read is carried by POST there just as a write would be; that
is transport, not intent. This script sends queries and refuses any document
containing a mutation or a subscription before it opens a socket. Nothing is
written and the repair is printed rather than performed.

Every connection in the schema caps first and last at 100. Over that the query
is rejected during validation, before execution begins, which is why the body
comes back with an errors array and no data key at all and why nothing is
billed. The number being rejected is often not written in the document: it
arrives through a variable default or through the variables map, so a text
search for a literal over 100 reports a clean document that fails every call.

What this can and cannot see: the ceiling is a fact about the schema and the
audit is a fact about the text plus the variables you hand it. A value computed
by a caller at run time is not in either, so the script reports an unresolved
argument as unresolved rather than as safe.

Environment:

    GITHUB_TOKEN    a token with read access to the GraphQL API. Not needed
                    with --offline.
"""
import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_graphql_slice")

API = "https://api.github.com"
UA = "github-graphql-slice/1.0"

# The ceiling on first and last, on every connection in the schema. Not a
# policy, not adjustable, and the same on every plan.
CEILING = 100

# A simple query costs one point, and a query rejected during validation costs
# none at all because it never runs.
POINTS_PER_QUERY = 1

# The default deliberately hides its oversized value in a variable default,
# because that is the version of this bug a grep does not find.
DEFAULT_QUERY = (
    "query($owner: String!, $name: String!, $first: Int = 250) {"
    " repository(owner: $owner, name: $name) {"
    " issues(first: $first, states: OPEN) { totalCount nodes { number title } }"
    " } }"
)

ARGUMENT_IN_MESSAGE = re.compile(
    "Argument '([A-Za-z_][A-Za-z0-9_]*)' on Field '([A-Za-z_][A-Za-z0-9_]*)'")


def strip_noise(document):
    """Remove GraphQL comments and string literals from a document. Pure.

    Written as a scanner rather than a regex because a hash inside a string
    literal is a legitimate character and a comment marker outside one.
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
    """Why this document will not be sent, or None if it is a read. Pure.

    The endpoint is the same one mutations go to, so the guard lives here rather
    than in a comment.
    """
    ops = operations(document)
    if not ops:
        return "the document contains no operation to send."
    for kind in ("mutation", "subscription"):
        if kind in ops:
            return ("the document contains a %s. This script sends queries only: "
                    "a query is a read, and the section it belongs to promises "
                    "its scripts never write." % kind)
    return None


def argument_value(argument_text, name):
    """The text written for one named argument, or None. Pure.

    Splits on top-level commas so a nested object or list argument does not
    confuse the scan, and requires an exact name match so a variable definition
    such as `$first: Int = 250` is never mistaken for an argument called first.
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
    for part in parts:
        key, sep, value = part.partition(":")
        if not sep:
            continue
        if key.strip() == name:
            return value.strip() or None
    return None


def variable_defaults(document):
    """Defaults declared in the operation's variable definitions. Pure.

    Keyed with the leading dollar so the keys read the way they are written in
    the document: {"$first": "250"}.
    """
    src = strip_noise(document)
    head = src.split("{", 1)[0]
    out = {}
    for part in head.replace("(", " ").replace(")", " ").split(","):
        name, sep, rest = part.partition(":")
        if not sep:
            continue
        # The first part still carries the operation keyword and name in front
        # of the variable, so take the last token rather than the whole thing.
        name = (name.strip().rsplit(None, 1) or [""])[-1]
        if not name.startswith("$") or "=" not in rest:
            continue
        out[name] = rest.split("=", 1)[1].strip()
    return out


def slicing_arguments(document):
    """Every first and last in the document, with the field carrying it. Pure.

    Depth is counted in selection sets, so a reader can see which connection is
    where without reading the query again.
    """
    src = strip_noise(document)
    out, i, n, depth, word = [], 0, len(src), 0, ""
    while i < n:
        ch = src[i]
        if ch.isalnum() or ch == "_":
            word += ch
            i += 1
            continue
        if ch == "(" and word:
            field, j, level = word, i, 0
            while j < n:
                if src[j] == "(":
                    level += 1
                elif src[j] == ")":
                    level -= 1
                    if level == 0:
                        break
                j += 1
            args = src[i + 1:j]
            for arg in ("first", "last"):
                raw = argument_value(args, arg)
                if raw is not None:
                    out.append({"field": field, "arg": arg, "raw": raw, "depth": depth})
            i, word = j + 1, ""
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        word = ""
        i += 1
    return out


def as_int(value):
    """An integer, or None if this is not one. Pure."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def resolve_slice(raw, defaults=None, variables=None):
    """One written slicing value resolved to (value, source). Pure.

    Three sources in the order that decides the call: a literal in the document,
    a value supplied in the variables map, and a default in the operation's
    variable definitions. A supplied value beats a default because that is what
    the server sees.
    """
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


def verdict(value):
    """One resolved value against the ceiling. Pure."""
    if value is None:
        return "unresolved"
    if value < 1:
        return "below-one"
    if value > CEILING:
        return "over-ceiling"
    if value == CEILING:
        return "at-ceiling"
    return "under-ceiling"


def pages_needed(value):
    """Round trips at 100 per page for a requested size. Pure."""
    if value is None or value < 1:
        return None
    return -(-value // CEILING)


def audit(document, variables=None):
    """Every slicing argument, resolved and judged. Pure."""
    defaults = variable_defaults(document)
    out = []
    for found in slicing_arguments(document):
        value, source = resolve_slice(found["raw"], defaults, variables)
        out.append({
            "field": found["field"],
            "arg": found["arg"],
            "depth": found["depth"],
            "written": found["raw"],
            "value": value,
            "source": source,
            "verdict": verdict(value),
            "pages": pages_needed(value),
        })
    return out


def classify(findings):
    """Classify a whole document. Pure. Returns (state, detail)."""
    if not findings:
        return ("no-slicing-argument",
                "no first or last appears anywhere in this document. GitHub "
                "requires a slicing argument on every connection, so either "
                "there is no connection here or the query is rejected for a "
                "different reason than this note describes.")
    over = [f for f in findings if f["verdict"] == "over-ceiling"]
    literal = [f for f in over if f["source"] == "literal"]
    if literal:
        f = literal[0]
        return ("over-ceiling-in-the-document",
                "%s.%s asks for %d, which is over the ceiling of %d, and the "
                "number is written in the query."
                % (f["field"], f["arg"], f["value"], CEILING))
    if over:
        f = over[0]
        return ("over-ceiling-through-a-variable",
                "%s.%s resolves to %d through a %s, so a search of the document "
                "for a number over %d finds nothing and every call is still "
                "rejected." % (f["field"], f["arg"], f["value"], f["source"], CEILING))
    unresolved = [f for f in findings if f["verdict"] == "unresolved"]
    if unresolved:
        f = unresolved[0]
        return ("unresolved-slice",
                "%s.%s is written as %s and no default or supplied value "
                "explains it, so this document cannot be cleared from the text "
                "alone." % (f["field"], f["arg"], f["written"]))
    below = [f for f in findings if f["verdict"] == "below-one"]
    if below:
        f = below[0]
        return ("slice-below-one",
                "%s.%s resolves to %d. The range is 1 to %d and zero is "
                "rejected the same way an oversized value is."
                % (f["field"], f["arg"], f["value"], CEILING))
    return ("within-the-ceiling",
            "all %d slicing argument(s) resolve to between 1 and %d. This "
            "document is not rejected for an argument value; the node count is "
            "a separate question." % (len(findings), CEILING))


def repair(state):
    """The sentence a reader has to act on. Pure."""
    if state == "over-ceiling-in-the-document":
        return ("set the value to 100 and page with after: $cursor until "
                "pageInfo.hasNextPage is false. The ceiling is not adjustable.")
    if state == "over-ceiling-through-a-variable":
        return ("fix the value where it is set, not in the query text. Cap it "
                "at 100 in the caller or in the variable default, and page "
                "with after: $cursor for the rest.")
    if state == "unresolved-slice":
        return ("run this again with --variables so the value can be resolved. "
                "An argument nobody can resolve is not an argument anybody has "
                "checked.")
    if state == "slice-below-one":
        return ("use a value of at least 1. A slicing argument of 0 is not a "
                "cheap query, it is a rejected one.")
    if state == "within-the-ceiling":
        return ("nothing on the argument ceiling. Check the node count as well: "
                "see /github/graphql-node-limit-exceeded/ -- a document legal on "
                "every argument can still be rejected for the product of them.")
    return "point the check at a document containing a connection."


def error_phase(status, body):
    """Which phase of the request failed. Pure.

    The specification is precise here and the distinction is the whole reason
    this note has a different shape from the errors-array notes: a request that
    fails before execution must not carry a data entry at all, while one that
    fails during execution carries data with nulls in it.
    """
    if not isinstance(body, dict):
        return "unreadable"
    if not body.get("errors"):
        return "clean"
    if "data" not in body:
        return "validation"
    return "execution"


def offending_argument(body):
    """The (argument, field) the server named, or (None, None). Pure."""
    if not isinstance(body, dict):
        return (None, None)
    for err in body.get("errors") or []:
        message = err.get("message", "") if isinstance(err, dict) else ""
        m = ARGUMENT_IN_MESSAGE.search(message)
        if m:
            return (m.group(1), m.group(2))
    return (None, None)


def point_cost(sending):
    """Points this run can spend. Pure."""
    return POINTS_PER_QUERY if sending else 0


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
    ap.add_argument("--file", help="a .graphql file to audit")
    ap.add_argument("--query", help="the document as a string")
    ap.add_argument("--variables", default="{}",
                    help="JSON object of the variables you actually send")
    ap.add_argument("--repo", help="owner/name, to fill the default query")
    ap.add_argument("--offline", action="store_true",
                    help="audit the text only. No token, nothing sent.")
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

    if args.repo:
        try:
            owner, name = args.repo.split("/", 1)
        except ValueError:
            log.error("--repo takes owner/name")
            return 2
        variables.setdefault("owner", owner)
        variables.setdefault("name", name)

    why_not = refusal(document)
    if why_not:
        log.error("refusing to send: %s", why_not)
        return 2

    findings = audit(document, variables)
    log.info("point cost: %d point(s). The ceiling is checked against the query "
             "text and the variables you supply; a document rejected during "
             "validation never executes and is not billed at all.",
             point_cost(not args.offline))
    for f in findings:
        log.info("  %s.%s  written %s  value %s  %s  %s",
                 f["field"], f["arg"], f["written"],
                 "?" if f["value"] is None else f["value"],
                 f["source"],
                 "OVER, needs %d pages" % f["pages"]
                 if f["verdict"] == "over-ceiling" else f["verdict"])
    state, detail = classify(findings)
    log.info("%s: %s", state, detail)
    log.info("repair: %s", repair(state))

    probe = None
    if not args.offline:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            log.error("set GITHUB_TOKEN, or pass --offline to audit the text only")
            return 2
        session = requests.Session()
        session.headers.update({
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            # GitHub rejects requests with no User-Agent outright.
            "User-Agent": UA,
        })
        status, body = run_query(session, document, variables)
        phase = error_phase(status, body)
        arg, field = offending_argument(body)
        log.info("HTTP %s, phase=%s, data key present=%s",
                 status, phase, "yes" if isinstance(body, dict) and "data" in body else "no")
        if arg:
            log.info("rejected argument: %s on field %s", arg, field)
        if phase == "validation":
            log.info("validation-rejected: the body carries errors and no data "
                     "key, which is what a failure before execution looks like")
        probe = {"status": status, "phase": phase,
                 "rejected_argument": arg, "rejected_field": field}

    print(json.dumps({"ceiling": CEILING, "state": state, "findings": findings,
                      "probe": probe}, indent=2, default=str))
    return 1 if state not in ("within-the-ceiling", "no-slicing-argument") else 0


if __name__ == "__main__":
    sys.exit(main())
