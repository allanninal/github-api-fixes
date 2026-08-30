"""Separate the fields a GraphQL response withheld from the ones that are empty.

Read only, and queries only. GitHub's GraphQL endpoint takes a document in the
request body, so a read is carried by POST there just as a write would be; that
is transport, not intent. This script sends queries and refuses any document
containing a mutation or a subscription before it opens a socket. Nothing is
written and the repair is printed rather than performed.

GraphQL resolves each field independently. A field the token cannot see becomes
null in data and adds an entry to errors carrying a path that names it exactly,
while the rest of the response succeeds. The response is genuinely partial. The
danger is that a withheld null and a real null look identical in the data and
mean opposite things: unknown versus none.

What this can and cannot see: the API has no idea whether your code aggregates
across the nulls. It can measure this response, name which nulls were explained
by an errors entry and say whether a sum over a given root is still honest.

Environment:

    GITHUB_TOKEN    a token with read access to the GraphQL API
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_graphql_partial")

API = "https://api.github.com"
UA = "github-graphql-partial/1.0"

POINTS_PER_QUERY = 1

# Deliberately a mixture. Two of these fields commonly resolve to null because a
# read-only token is not allowed to see them, and one commonly resolves to null
# because the repository really has nothing there. Telling those apart is the
# whole job.
DEFAULT_QUERY = (
    "query($owner: String!, $name: String!) {"
    " repository(owner: $owner, name: $name) {"
    " name isPrivate diskUsage"
    " licenseInfo { key }"
    " collaborators(first: 1) { totalCount }"
    " } }"
)

# What a withheld field would need to be readable. Not a promise that granting
# it is the right move: some of these are a much bigger decision than an
# under-count, which is exactly why the script names them instead of advising.
PERMISSION_HINT = {
    "diskUsage": "metadata read plus admin on the repository",
    "collaborators": "read access to repository members",
    "vulnerabilityAlerts": "Dependabot alerts read",
    "projectsV2": "organization projects read",
    "members": "read:org on the organization",
    "email": "user email read, and the user must have a public email",
}

MISSING = object()


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


def path_key(path):
    """A GraphQL error path rendered as a dotted string. Pure.

    List indices are kept as segments, because an error on element 3 of a
    connection is a different fact from an error on the connection.
    """
    if isinstance(path, str):
        return path
    return ".".join(str(p) for p in (path or []))


def value_at(data, dotted):
    """Resolve a dotted path in a data tree. Pure. MISSING if there is no such path."""
    cur = data
    if not dotted:
        return cur
    for seg in str(dotted).split("."):
        if isinstance(cur, dict):
            if seg not in cur:
                return MISSING
            cur = cur[seg]
        elif isinstance(cur, list):
            try:
                cur = cur[int(seg)]
            except (ValueError, IndexError):
                return MISSING
        else:
            return MISSING
    return cur


def null_paths(data, prefix=""):
    """Every path in a data tree whose value is null. Pure."""
    out = []
    if isinstance(data, dict):
        items = data.items()
    elif isinstance(data, list):
        items = ((str(i), v) for i, v in enumerate(data))
    else:
        return out
    for key, value in items:
        here = key if not prefix else prefix + "." + str(key)
        if value is None:
            out.append(here)
        else:
            out.extend(null_paths(value, here))
    return sorted(out)


def error_paths(body):
    """Dotted paths named by the errors array, mapped to their type. Pure."""
    out = {}
    if not isinstance(body, dict):
        return out
    for err in body.get("errors") or []:
        if not isinstance(err, dict) or not err.get("path"):
            continue
        out[path_key(err["path"])] = err.get("type") or "UNTYPED"
    return out


def unpathed_errors(body):
    """Errors that name no field, so nothing can be attributed to them. Pure."""
    if not isinstance(body, dict):
        return 0
    return sum(1 for e in (body.get("errors") or [])
               if not isinstance(e, dict) or not e.get("path"))


def has_usable_data(body):
    """Whether any top-level field resolved to something other than null. Pure."""
    if not isinstance(body, dict):
        return False
    data = body.get("data")
    if not isinstance(data, dict):
        return False
    return any(v is not None for v in data.values())


def withheld(body):
    """Paths that are null in data and explained by an errors entry. Pure."""
    if not isinstance(body, dict):
        return []
    named = error_paths(body)
    nulls = set(null_paths(body.get("data")))
    return sorted(p for p in named if p in nulls)


def absent(body):
    """Paths that are null with no errors entry: genuinely empty, not hidden. Pure."""
    if not isinstance(body, dict):
        return []
    named = set(error_paths(body))
    return sorted(p for p in null_paths(body.get("data")) if p not in named)


def orphan_error_paths(body):
    """Error paths that do not resolve to a null in data. Pure.

    Rare, and reported rather than swallowed: it usually means the path points
    into a list element that was dropped entirely, and a script that silently
    ignored it would be under-reporting the very thing it exists to count.
    """
    if not isinstance(body, dict):
        return []
    nulls = set(null_paths(body.get("data")))
    return sorted(p for p in error_paths(body) if p not in nulls)


def permission_hint(dotted):
    """The permission a withheld field would want. Pure."""
    leaf = str(dotted).split(".")[-1]
    return PERMISSION_HINT.get(leaf, "the permission that covers this field")


def tally(body):
    """Counts for one response. Pure."""
    return {
        "withheld": len(withheld(body)),
        "absent": len(absent(body)),
        "orphaned": len(orphan_error_paths(body)),
        "unpathed_errors": unpathed_errors(body),
    }


def is_partial_success(body):
    """Data survived and errors arrived beside it. Pure."""
    if not isinstance(body, dict):
        return False
    return bool(body.get("errors")) and has_usable_data(body)


def safe_to_aggregate(body, root):
    """Whether a sum under this root is honest. Pure. Returns (bool, sentence)."""
    under = [p for p in withheld(body)
             if not root or p == root or p.startswith(str(root) + ".")]
    if not under:
        return True, ("no withheld fields under %r, so a total over it is a "
                      "total." % root)
    return False, ("%d withheld field(s) under %r, so a total over it is a lower "
                   "bound and has to be labelled as one." % (len(under), root))


def classify(body):
    """Classify one response. Pure. Returns (state, detail).

    Total failure is named and handed on rather than absorbed, because the
    repair for a query that failed outright is not the repair for one that
    mostly worked.
    """
    if not isinstance(body, dict):
        return ("unreadable",
                "the response was not a JSON object, so nothing can be counted "
                "in it.")
    errs = body.get("errors") or []
    hidden = withheld(body)
    empty = absent(body)
    if errs and not has_usable_data(body):
        return ("total-failure",
                "%d error(s) arrived and no field resolved, so this is a failed "
                "query wearing a 200 rather than a partial one." % len(errs))
    if errs and not hidden and unpathed_errors(body):
        return ("errors-without-path",
                "%d error(s) arrived beside usable data but none of them names a "
                "field, so nothing can be attributed to a column."
                % unpathed_errors(body))
    if hidden:
        return ("partial-withheld",
                "%d field(s) resolved to null and errors[].path explains %s."
                % (len(hidden), "both" if len(hidden) == 2 else "each of them"))
    if empty:
        return ("nulls-unexplained",
                "%d null(s) in the data and no errors entry for any of them, so "
                "they are genuinely empty rather than withheld." % len(empty))
    return ("complete",
            "every requested field resolved and the errors array is empty.")


def repair(state):
    """The sentence a reader has to act on. Pure."""
    if state == "partial-withheld":
        return ("record the withheld paths as unknown, not zero, and label the "
                "total a lower bound. Do not retry: this token returns the same "
                "nulls every time.")
    if state == "nulls-unexplained":
        return ("nothing on the nulls: with no errors entry beside them they are "
                "real answers. Keep reading errors[].path anyway, because that "
                "is what will tell you when one of them stops being real.")
    if state == "total-failure":
        return ("see /github/graphql-200-with-errors/ -- nothing resolved here, "
                "so this is the total-failure case and partial-response handling "
                "does not apply.")
    if state == "errors-without-path":
        return ("log these errors verbatim and treat the whole response as "
                "suspect. An error with no path cannot be attributed to a "
                "column, so no per-field repair is available.")
    if state == "complete":
        return "nothing."
    return "point the check at a document this endpoint can answer."


def point_cost(queries):
    """Points this run will spend against the GraphQL budget. Pure."""
    try:
        return max(0, int(queries)) * POINTS_PER_QUERY
    except (TypeError, ValueError):
        return 0


def run_query(session, document, variables):
    """Send one query. Returns (status, body-or-None).

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


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--query",
                    help="send your own query document instead of the default. "
                         "Use the one your integration actually sends: the nulls "
                         "follow the fields. Mutations are refused.")
    ap.add_argument("--root", default="repository",
                    help="the path you aggregate over, checked for withheld "
                         "fields underneath it")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2
    try:
        owner, name = args.repo.split("/", 1)
    except ValueError:
        log.error("--repo takes owner/name")
        return 2

    document = args.query or DEFAULT_QUERY
    why_not = refusal(document)
    if why_not:
        log.error("refusing to send: %s", why_not)
        return 2

    log.info("point cost: %d point(s) against the 5,000/hour GraphQL budget",
             point_cost(1))

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "User-Agent": UA,
    })

    status, body = run_query(session, document, {"owner": owner, "name": name})
    state, detail = classify(body)
    counts = tally(body)
    named = error_paths(body)

    log.info("HTTP %s, errors=%d, data usable=%s", status,
             len(((body or {}).get("errors")) or []),
             "yes" if has_usable_data(body) else "no")
    log.info("%s: %s", state, detail)
    for p in withheld(body):
        log.info("  %-34s withheld  %-11s wants: %s",
                 p, named.get(p, "UNTYPED"), permission_hint(p))
    for p in absent(body):
        log.info("  %-34s absent    %-11s genuinely empty, safe to read as none",
                 p, "-")
    for p in orphan_error_paths(body):
        log.info("  %-34s orphaned  %-11s named by errors but not null in data",
                 p, named.get(p, "UNTYPED"))

    ok, sentence = safe_to_aggregate(body, args.root)
    log.info("aggregation over %r is %s: %s", args.root,
             "safe" if ok else "NOT safe", sentence)
    log.info("repair: %s", repair(state))

    print(json.dumps({
        "points_spent": point_cost(1),
        "status": status,
        "state": state,
        "detail": detail,
        "partial_success": is_partial_success(body),
        "withheld": withheld(body),
        "absent": absent(body),
        "orphan_error_paths": orphan_error_paths(body),
        "tally": counts,
        "aggregation_root": args.root,
        "aggregation_safe": ok,
    }, indent=2, default=str))
    return 1 if state in ("partial-withheld", "errors-without-path") else 0


if __name__ == "__main__":
    sys.exit(main())
