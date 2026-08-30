"""Prove the crosswalk between GraphQL node ids and REST database ids.

Read only, and queries only. GitHub's GraphQL endpoint takes its document in
the request body, so a read travels by POST there exactly as a write would;
that is a transport detail, not a licence to write. The document is parsed
before anything is sent and refused if it contains a mutation or a
subscription.

GraphQL's id is an opaque global node ID and REST's id is a numeric database
ID, and each response calls its own one "id". The mapping is exact: REST
node_id equals GraphQL id, and REST id equals GraphQL databaseId. A store that
takes whichever field arrived ends up with two key spaces for one entity and a
join that returns nothing.

What this can and cannot see: nothing GitHub returns knows what your schema
holds. This fetches one object down both paths to prove the crosswalk, then
classifies a sample of ids you supply so you can see which spaces your own
column contains.

Environment:

    GITHUB_TOKEN    a token with read access to the repository
"""
import argparse
import base64
import binascii
import json
import logging
import os
import re
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_graphql_id_crosswalk")

API = "https://api.github.com"
UA = "github-graphql-id-crosswalk/1.0"

# One repository plus one issue in a single document costs one point.
POINTS_PER_QUERY = 1

# The legacy global node ID is base64 of "<length>:<Type><databaseId>", so
# MDU6SXNzdWUxMzQ3 decodes to 05:Issue1347. The newer format is opaque and
# carries nothing recoverable, which is why decoding is never a migration plan.
LEGACY_DECODED = re.compile(r"^(\d+):([A-Za-z]+)(\d+)$")
NEW_NODE_ID = re.compile(r"^[A-Za-z]{1,4}_[A-Za-z0-9_-]{8,}$")
ALL_DIGITS = re.compile(r"^\d+$")

ISSUE_QUERY = (
    "query($owner: String!, $name: String!, $number: Int!) {"
    " repository(owner: $owner, name: $name) {"
    " id databaseId"
    " issue(number: $number) { id databaseId number } } }"
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


def decode_legacy_node_id(value):
    """(type, database_id) for a legacy node ID, or None. Pure.

    The legacy format is base64 of "<length>:<Type><databaseId>" where the
    length is the length of the type name. Checking that length is what stops
    an ordinary base64 string being read as an identifier.
    """
    text = str(value or "")
    if not text or ALL_DIGITS.match(text):
        return None
    padded = text + "=" * (-len(text) % 4)
    try:
        raw = base64.b64decode(padded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    m = LEGACY_DECODED.match(raw)
    if not m:
        return None
    declared_len, type_name, database_id = m.group(1), m.group(2), m.group(3)
    if int(declared_len) != len(type_name):
        return None
    return (type_name, int(database_id))


def id_space(value):
    """Which key space a stored identifier belongs to. Pure.

    Returns "rest-database-id", "graphql-node-id" or "unknown". An integer or
    an all-digit string is a database ID; anything that decodes as a legacy
    node ID or matches the newer opaque shape is a node ID.
    """
    if isinstance(value, bool):
        return "unknown"
    if isinstance(value, int):
        return "rest-database-id"
    text = str(value or "").strip()
    if not text:
        return "unknown"
    if ALL_DIGITS.match(text):
        return "rest-database-id"
    if decode_legacy_node_id(text) or NEW_NODE_ID.match(text):
        return "graphql-node-id"
    return "unknown"


def to_database_id(value):
    """The numeric key for this identifier without a network call, or None. Pure.

    Legacy node IDs carry the number and can be migrated offline. New-format
    ones carry nothing and have to be re-fetched, which is the difference that
    decides how large the migration is.
    """
    space = id_space(value)
    if space == "rest-database-id":
        return int(str(value).strip())
    decoded = decode_legacy_node_id(value)
    return decoded[1] if decoded else None


def crosswalk(rest_object, graphql_object):
    """Compare one object fetched both ways. Pure. Returns a dict of facts."""
    rest_object = rest_object if isinstance(rest_object, dict) else {}
    graphql_object = graphql_object if isinstance(graphql_object, dict) else {}
    rest_id = rest_object.get("id")
    rest_node_id = rest_object.get("node_id")
    gql_id = graphql_object.get("id")
    gql_database_id = graphql_object.get("databaseId")
    return {
        "rest_id": rest_id,
        "rest_node_id": rest_node_id,
        "rest_number": rest_object.get("number"),
        "graphql_id": gql_id,
        "graphql_database_id": gql_database_id,
        "graphql_number": graphql_object.get("number"),
        "node_ids_match": bool(rest_node_id) and rest_node_id == gql_id,
        "database_ids_match": rest_id is not None and rest_id == gql_database_id,
        "database_id_present": gql_database_id is not None,
    }


def number_is_not_the_database_id(rest_object):
    """Whether an object's number and database id differ. Pure.

    They usually do, and both are integers, so a column typed for one accepts
    the other silently. Where they happen to be equal the warning still stands.
    """
    rest_object = rest_object if isinstance(rest_object, dict) else {}
    number, database_id = rest_object.get("number"), rest_object.get("id")
    if number is None or database_id is None:
        return None
    return number != database_id


def classify_pair(rest_object, graphql_object):
    """Judge one crosswalk. Pure. Returns (state, detail)."""
    facts = crosswalk(rest_object, graphql_object)
    if facts["rest_id"] is None or facts["graphql_id"] is None:
        return ("incomplete",
                "one of the two responses did not carry an identifier, so "
                "nothing can be compared.")
    if not facts["database_id_present"]:
        return ("database-id-absent",
                "this type exposes no databaseId, so the node ID is the only "
                "key it has. A store that requires an integer has no row to "
                "write for it.")
    if facts["node_ids_match"] and facts["database_ids_match"]:
        return ("crosswalk-confirmed",
                "REST node_id equals GraphQL id, and REST id equals GraphQL "
                "databaseId.")
    return ("crosswalk-broken",
            "the two responses disagree, which means they are not the same "
            "object. Check that the number and the query are pointing at one "
            "thing before reading anything into the ids.")


def classify_store(values):
    """Judge a sample of stored identifiers. Pure. Returns (state, detail)."""
    values = list(values or [])
    if not values:
        return ("no-sample", "no identifiers were supplied to classify.")
    counts = {"rest-database-id": 0, "graphql-node-id": 0, "unknown": 0}
    for v in values:
        counts[id_space(v)] += 1
    if counts["rest-database-id"] and counts["graphql-node-id"]:
        return ("mixed-key-space",
                "one entity type is keyed two ways in the same column: %d "
                "database id(s) and %d node id(s)."
                % (counts["rest-database-id"], counts["graphql-node-id"]))
    if counts["unknown"] == len(values):
        return ("unrecognised",
                "none of these look like either key space. They may be your "
                "own surrogate keys, which is fine and not this note.")
    if counts["graphql-node-id"]:
        return ("consistent-node-id",
                "every identifier is a global node ID. Read node_id from REST "
                "responses to keep it that way.")
    return ("consistent-database-id",
            "every identifier is a numeric database ID. Request databaseId "
            "explicitly in every GraphQL selection to keep it that way.")


def join_rows(left, right):
    """How many identifiers appear in both lists, compared as given. Pure."""
    return len({str(v) for v in (left or [])} & {str(v) for v in (right or [])})


def join_rows_normalised(left, right):
    """The same join after both sides are reduced to database ids. Pure.

    New-format node IDs drop out, because nothing local can turn one into a
    number. That is the honest answer and it is why the count can still be
    short after normalising.
    """
    def keys(values):
        out = set()
        for v in values or []:
            k = to_database_id(v)
            if k is not None:
                out.add(k)
        return out
    return len(keys(left) & keys(right))


def migration_split(values):
    """How many stored ids can be rewritten offline, and how many cannot. Pure."""
    offline, refetch, already = 0, 0, 0
    for v in values or []:
        space = id_space(v)
        if space == "rest-database-id":
            already += 1
        elif space == "graphql-node-id":
            if to_database_id(v) is None:
                refetch += 1
            else:
                offline += 1
    return {"already_numeric": already, "decodable_offline": offline,
            "needs_refetching": refetch}


def repair(state):
    """The sentence a reader has to act on. Pure."""
    if state == "mixed-key-space":
        return ("pick one key space, request databaseId in every GraphQL "
                "selection or node_id from every REST response, and migrate "
                "the rows you hold. Do not join across the two.")
    if state == "crosswalk-broken":
        return ("stop and confirm both calls address the same object. An "
                "issue's number is not its databaseId, and using one where "
                "the other belongs is the usual cause.")
    if state == "database-id-absent":
        return ("key this entity by its node ID. There is no integer to store "
                "and decoding the node ID will not produce one.")
    if state == "consistent-node-id":
        return ("nothing to migrate. Keep reading node_id on the REST side so "
                "a new code path cannot introduce the other space.")
    if state == "consistent-database-id":
        return ("nothing to migrate. Keep asking for databaseId on the GraphQL "
                "side so a new code path cannot introduce the other space.")
    if state == "unrecognised":
        return ("nothing here is a GitHub identifier. Point the sample at the "
                "column that holds them.")
    return ("fetch one object down both paths and compare the four fields "
            "before changing any schema.")


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
    ap.add_argument("--issue", type=int, required=True,
                    help="an issue NUMBER, which is not its database id")
    ap.add_argument("--ids", default="",
                    help="comma-separated identifiers from your own store")
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

    why_not = refusal(ISSUE_QUERY)
    if why_not:
        log.error("refusing to send: %s", why_not)
        return 2

    log.info("point cost: %d point(s) against the 5,000/hour GraphQL budget, "
             "plus 1 core request", POINTS_PER_QUERY)

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })

    r = session.get("%s/repos/%s/%s/issues/%d" % (API, owner, name, args.issue),
                    timeout=30)
    rest_object = r.json() if r.status_code == 200 else {}
    if r.status_code != 200:
        log.error("REST read failed with HTTP %s; the crosswalk needs both "
                  "sides", r.status_code)

    status, body = run_query(session, ISSUE_QUERY,
                             {"owner": owner, "name": name, "number": args.issue})
    repository = ((body or {}).get("data") or {}).get("repository") or {}
    graphql_object = repository.get("issue") or {}
    if isinstance(body, dict) and body.get("errors"):
        log.error("the query carried errors: %s",
                  json.dumps(body["errors"])[:300])

    log.info("rest:    id=%s  node_id=%s  number=%s", rest_object.get("id"),
             rest_object.get("node_id"), rest_object.get("number"))
    log.info("graphql: databaseId=%s  id=%s  number=%s",
             graphql_object.get("databaseId"), graphql_object.get("id"),
             graphql_object.get("number"))

    state, detail = classify_pair(rest_object, graphql_object)
    log.info("%s: %s", state, detail)
    differs = number_is_not_the_database_id(rest_object)
    if differs is not None:
        log.info("number and databaseId are both integers and are %s on this "
                 "object; they are never the same field",
                 "different" if differs else "equal by coincidence")
    log.info("repair: %s", repair(state))

    sample = [v.strip() for v in args.ids.split(",") if v.strip()]
    store_state, store_detail = classify_store(sample)
    split = migration_split(sample)
    if sample:
        log.info("store sample: %s", ", ".join(
            "%s -> %s" % (v, id_space(v)) for v in sample))
        log.info("%s: %s", store_state, store_detail)
        log.info("migratable offline: %d    needs re-fetching: %d    already "
                 "numeric: %d", split["decodable_offline"],
                 split["needs_refetching"], split["already_numeric"])
        log.info("repair: %s", repair(store_state))

    print(json.dumps({
        "points_spent": POINTS_PER_QUERY,
        "http_status": status,
        "crosswalk": crosswalk(rest_object, graphql_object),
        "state": state,
        "detail": detail,
        "store_state": store_state,
        "store_detail": store_detail,
        "migration": split,
    }, indent=2, default=str))
    return 1 if state in ("crosswalk-broken",) or store_state == "mixed-key-space" else 0


if __name__ == "__main__":
    sys.exit(main())
