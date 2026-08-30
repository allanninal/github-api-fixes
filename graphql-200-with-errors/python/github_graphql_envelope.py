"""Show that a GraphQL 200 can carry an errors array a status check walks past.

Read only, and queries only. GitHub's GraphQL endpoint takes a document in the
request body, so a read is carried by POST there just as a write would be; that
is a transport detail, not a licence to write. This script sends queries and
refuses any document containing a mutation or a subscription before it opens a
socket. Nothing is written and the repair is printed rather than performed.

GraphQL reports application failures in the response body. A query that hit a
missing repository, a permission the token lacks, an exhausted point budget or a
query too large to run still returns 200 OK with an errors array beside a null
data field. Error handling written around HTTP status codes sees a success.

What this can and cannot see: the API has no idea whether your client reads the
errors array. What it can do is make the endpoint produce the shape on demand
and print both predicates over the same response so you can compare them against
your own code. That is the trap, not the fall.

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
log = logging.getLogger("github_graphql_envelope")

API = "https://api.github.com"
UA = "github-graphql-envelope/1.0"

# A simple query costs one point. Named because it is printed before anything is
# spent, and a reader comparing this against the documentation should find it in
# one place.
POINTS_PER_QUERY = 1

DEFAULT_QUERY = (
    "query($owner: String!, $name: String!) {"
    " repository(owner: $owner, name: $name) { name isPrivate } }"
)

# The behaviours the five documented error types actually demand. Two of these
# are retryable, one is a permission change, one is a query change that no
# amount of retrying will fix, and one is a fact about the world.
BEHAVIOUR = {
    "RATE_LIMITED": ("wait", "the point budget is spent. Wait for the reset that "
                             "GET /rate_limit reports and do not retry before it."),
    "FORBIDDEN": ("alert", "the token cannot see this. Retrying changes nothing; "
                           "a human has to widen the permission or accept the gap."),
    "NOT_FOUND": ("record-absent", "the resource is missing or invisible to this "
                                   "token. Record the absence; do not treat it as zero."),
    "MAX_NODE_LIMIT_EXCEEDED": ("reshape", "the query asks for too many nodes and "
                                           "will fail identically every time. Lower "
                                           "the first values and paginate."),
    "INTERNAL": ("retry-once", "a failure on GitHub's side. Retry once with backoff, "
                               "then give up and log the query."),
    "SERVICE_UNAVAILABLE": ("retry-once", "a transient failure on GitHub's side. "
                                          "Retry once with backoff."),
}


def strip_noise(document):
    """Remove GraphQL comments and string literals from a document. Pure.

    Written as a scanner rather than a regex because a hash inside a string
    literal is a legitimate character and a comment marker outside one, and a
    single pattern that gets that right is harder to read than this loop.
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
    "subscription" or "fragment". An anonymous document is the query shorthand.
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

    The endpoint is the same one mutations go to, so the guard lives here rather
    than in a comment. A section that promises its scripts never write has to
    mean it on the one endpoint where writing is a body away.
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


def status_says_ok(status):
    """The predicate a status-code client uses. Pure."""
    try:
        return 200 <= int(status) < 300
    except (TypeError, ValueError):
        return False


def envelope_says_ok(body):
    """The predicate a correct client uses. Pure."""
    if not isinstance(body, dict):
        return False
    return not body.get("errors")


def error_types(body):
    """The type of every entry in the errors array, in order. Pure.

    An entry with no type is reported as UNTYPED rather than dropped, because a
    handler keyed on type has to have something to fall through to.
    """
    if not isinstance(body, dict):
        return []
    out = []
    for err in body.get("errors") or []:
        if isinstance(err, dict):
            out.append(err.get("type") or "UNTYPED")
        else:
            out.append("UNTYPED")
    return out


def has_usable_data(body):
    """Whether any field in data resolved to something other than null. Pure."""
    if not isinstance(body, dict):
        return False
    data = body.get("data")
    if not isinstance(data, dict):
        return False
    return any(v is not None for v in data.values())


def predicates_disagree(status, body):
    """Whether a status check would pass on a response the envelope fails."""
    return status_says_ok(status) and not envelope_says_ok(body)


def behaviour_for(error_type):
    """What one error type demands of a client. Pure. Returns (action, detail)."""
    if error_type in BEHAVIOUR:
        return BEHAVIOUR[error_type]
    return ("log-verbatim",
            "an error type this script does not know. Log it verbatim and fail "
            "the call rather than guessing; new types get added over time.")


def classify(status, body):
    """Classify one response envelope. Pure. Returns (state, detail).

    The two states that both carry errors are kept apart on purpose. One is a
    call that failed and the other is a call that mostly worked, and giving the
    same advice for both throws away good data.
    """
    if not isinstance(body, dict):
        return ("unreadable",
                "the response was not a JSON object, so neither predicate can be "
                "evaluated over it.")
    if not status_says_ok(status):
        return ("transport-failure",
                "HTTP %s, which a status check already catches. The errors array "
                "is not where this one hides." % status)
    types = error_types(body)
    if not types:
        return ("200-clean",
                "the status line and the errors array agree that this worked. "
                "Both predicates pass, which on this response is agreement rather "
                "than proof that your client checks the second one.")
    if has_usable_data(body):
        return ("200-with-errors-and-data",
                "%d error(s) of type %s arrived with usable data, which is partial "
                "success and a different repair."
                % (len(types), ", ".join(sorted(set(types)))))
    return ("200-with-errors-no-data",
            "the status line says success and the body carries %d error(s) of type "
            "%s with no usable data."
            % (len(types), ", ".join(sorted(set(types)))))


def repair(state):
    """The sentence a reader has to act on. Pure."""
    if state == "200-with-errors-no-data":
        return ("read body.errors before body.data and branch on errors[].type. "
                "Put the check in the function that sends queries so no caller "
                "can skip it.")
    if state == "200-with-errors-and-data":
        return ("see /github/graphql-partial-data-nulls/ -- do not retry this "
                "one. Some fields resolved and discarding them because the call "
                "carried errors loses data that arrived correctly.")
    if state == "transport-failure":
        return ("handle the status code as you already do. This note is about "
                "the failures that arrive as a 200.")
    if state == "200-clean":
        return ("nothing on this response. Check that the errors array is read "
                "at all: the two predicates agree here and part company on the "
                "first failure.")
    return "point the check at a document this endpoint can answer."


def point_cost(probes):
    """Points this run will spend against the GraphQL budget. Pure."""
    return len(probes or []) * POINTS_PER_QUERY


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
    ap.add_argument("--repo", required=True,
                    help="owner/name to probe. A repository that does not exist "
                         "is the cheapest way to see the shape.")
    ap.add_argument("--query",
                    help="send your own query document instead of the default. "
                         "Mutations and subscriptions are refused.")
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

    probes = [
        ("missing-repository", document,
         {"owner": owner, "name": name + "-does-not-exist-probe"}),
        ("as-configured", document, {"owner": owner, "name": name}),
    ]
    log.info("point cost: %d point(s) against the 5,000/hour GraphQL budget",
             point_cost(probes))

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })

    findings = []
    for label, doc, variables in probes:
        status, body = run_query(session, doc, variables)
        state, detail = classify(status, body)
        types = error_types(body)
        log.info("probe %s: HTTP %s, errors=%d, data present=%s",
                 label, status, len(types), "yes" if has_usable_data(body) else "no")
        log.info("%s: %s", state, detail)
        log.info("status check passes: %s    envelope check passes: %s    "
                 "they disagree: %s",
                 "yes" if status_says_ok(status) else "no",
                 "yes" if envelope_says_ok(body) else "no",
                 "yes" if predicates_disagree(status, body) else "no")
        for t in sorted(set(types)):
            action, why = behaviour_for(t)
            log.info("  %s -> %s: %s", t, action, why)
        log.info("repair: %s", repair(state))

        findings.append({
            "probe": label,
            "status": status,
            "error_types": types,
            "has_usable_data": has_usable_data(body),
            "status_check_passes": status_says_ok(status),
            "envelope_check_passes": envelope_says_ok(body),
            "predicates_disagree": predicates_disagree(status, body),
            "behaviours": {t: behaviour_for(t)[0] for t in sorted(set(types))},
            "state": state,
            "detail": detail,
        })

    print(json.dumps({"points_spent": point_cost(probes), "findings": findings},
                     indent=2, default=str))
    return 1 if any(f["predicates_disagree"] for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
