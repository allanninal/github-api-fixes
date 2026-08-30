"""Work out which permission a fine-grained token is missing.

Read only. Every call here is a GET except the single GraphQL query, and the
GraphQL endpoint takes its document in the request body, so a read travels by
POST there exactly as a write would; that is a transport detail, not a licence
to write. The document is parsed first and refused if it contains a mutation or
a subscription.

A fine-grained personal access token carries per-resource permissions rather
than scopes. When one is missing the answer is 403 "Resource not accessible by
personal access token", which names nothing. The refusing response does carry
x-accepted-github-permissions, naming what the endpoint accepts, but there is
no header at all for what the token holds: fine-grained tokens send no
x-oauth-scopes, and that absence is how you tell one from a classic token.

So one half of the diff is read and the other half is measured, with one cheap
request per permission. A 200 is a grant and a 403 with this message is a
refusal; a 404 is neither, because GitHub hides 403s behind 404s.

Environment:

    GITHUB_TOKEN    the fine-grained token you are diagnosing
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_fine_grained_pat_probe")

API = "https://api.github.com"
UA = "github-fine-grained-pat-probe/1.0"

POINTS_PER_QUERY = 1

# Token prefixes GitHub documents. Only the prefix is ever printed; the token
# itself comes from the environment and never appears in output.
TOKEN_PREFIXES = [
    ("github_pat_", "fine-grained personal access token"),
    ("ghp_", "classic personal access token"),
    ("gho_", "OAuth user token"),
    ("ghu_", "GitHub App user-to-server token"),
    ("ghs_", "GitHub App installation token"),
    ("ghr_", "GitHub App refresh token"),
]

# One cheap read per fine-grained permission, all of them GETs that return at
# most one item. The permission name is the one shown on the token's settings
# page, so the repair can be followed without translation.
PROBES = [
    ("metadata", "/repos/{owner}/{repo}", "Metadata"),
    ("contents", "/repos/{owner}/{repo}/contents/", "Contents"),
    ("issues", "/repos/{owner}/{repo}/issues?per_page=1", "Issues"),
    ("pull_requests", "/repos/{owner}/{repo}/pulls?per_page=1", "Pull requests"),
    ("actions", "/repos/{owner}/{repo}/actions/workflows?per_page=1", "Actions"),
]

# The GraphQL twin of the issues probe, sent to show the same refusal arriving
# with no header attached to it.
ISSUES_QUERY = (
    "query($owner: String!, $name: String!) {"
    " repository(owner: $owner, name: $name) {"
    " issues(first: 1) { totalCount } } }"
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


def token_kind(token):
    """The credential type named by the token's prefix. Pure."""
    text = str(token or "")
    for prefix, label in TOKEN_PREFIXES:
        if text.startswith(prefix):
            return label
    return "unrecognised credential"


def token_prefix(token):
    """The prefix alone, safe to print. Pure."""
    text = str(token or "")
    for prefix, _label in TOKEN_PREFIXES:
        if text.startswith(prefix):
            return prefix
    return "none"


def scope_header_state(headers):
    """Whether x-oauth-scopes arrived, and empty or not. Pure.

    A classic or OAuth token always sends this header, even as an empty string
    when the token holds no scopes. A fine-grained token never sends it at all.
    Present-but-empty and absent therefore mean completely different things and
    are the one signal that separates the two credential families from the
    response alone.
    """
    if not isinstance(headers, dict):
        return "absent"
    for key, value in headers.items():
        if str(key).lower() == "x-oauth-scopes":
            return "present-empty" if str(value).strip() == "" else "present"
    return "absent"


def identify(token, headers):
    """What credential this is, from the prefix and the header. Pure.

    Returns (kind, detail). The two signals agreeing is worth stating, because
    a disagreement usually means the header was captured from a different call
    than the token was used on.
    """
    kind = token_kind(token)
    state = scope_header_state(headers)
    fine_grained = kind.startswith("fine-grained")
    if fine_grained and state == "absent":
        return (kind,
                "prefix %s and no x-oauth-scopes header, which a classic token "
                "always sends even when it is empty." % token_prefix(token))
    if fine_grained:
        return (kind,
                "prefix says fine-grained but an x-oauth-scopes header arrived, "
                "which fine-grained tokens do not send. Check that the header "
                "came from a call made with this token.")
    if state in ("present", "present-empty"):
        return (kind,
                "an x-oauth-scopes header arrived, so this credential carries "
                "scopes rather than fine-grained permissions.")
    return (kind, "no x-oauth-scopes header and no fine-grained prefix.")


def parse_accepted_permissions(value):
    """Parse x-accepted-github-permissions. Pure.

    Returns a list of alternatives, each a list of (permission, level) pairs
    that are required together. A comma separates alternatives, any one of
    which is sufficient; a semicolon joins permissions that are all required.
    Flattening the two is how somebody ends up granting more than the endpoint
    ever asked for.
    """
    out = []
    for alternative in str(value or "").split(","):
        pairs = []
        for clause in alternative.split(";"):
            clause = clause.strip()
            if not clause:
                continue
            name, _, level = clause.partition("=")
            pairs.append((name.strip(), level.strip() or "read"))
        if pairs:
            out.append(pairs)
    return out


def actor_from_message(message):
    """Which credential the refusal blames. Pure.

    The routing decision for the whole note: the same 403 body names the actor,
    and each actor has a different place to go and fix it.
    """
    text = str(message or "").lower()
    if "personal access token" in text:
        return "fine-grained-pat"
    if "by integration" in text:
        return "github-app"
    if "oauth app" in text or "oauth application" in text:
        return "oauth-app"
    return None


def grant_from_probe(status, message):
    """What one probe proves about one permission. Pure.

    Three outcomes, not two. A 404 proves nothing, because GitHub answers 404
    rather than 403 for resources a token cannot see, and a matrix that reads
    404 as "not granted" sends people to tick the wrong box.
    """
    try:
        code = int(status)
    except (TypeError, ValueError):
        return ("error", "no status to read.")
    if 200 <= code < 300:
        return ("granted", "the read succeeded, so this permission is held.")
    if code == 403 and actor_from_message(message) == "fine-grained-pat":
        return ("refused", "403 naming the personal access token, so this "
                           "permission is not held.")
    if code == 403:
        return ("refused-other", "403 that does not name a personal access "
                                 "token. Read the message: another actor or "
                                 "another rule refused this.")
    if code == 404:
        return ("ambiguous", "a 404 can hide a 403; see "
                             "/github/404-masking-403/ before concluding "
                             "anything from this row.")
    if code == 401:
        return ("unauthenticated", "the token itself was rejected, which is a "
                                   "credential problem rather than a "
                                   "permission one.")
    return ("error", "HTTP %s, which is neither a grant nor a refusal." % code)


def classify(status, message, headers, token, org_only=False):
    """Judge one refusal. Pure. Returns (state, detail)."""
    kind, _detail = identify(token, headers)
    actor = actor_from_message(message)
    try:
        code = int(status)
    except (TypeError, ValueError):
        code = 0
    if 200 <= code < 300:
        return ("clean", "this call was not refused.")
    if actor == "github-app":
        return ("not-this-note-app",
                "the message names an integration, so this is a GitHub App "
                "installation token and its permissions are readable through "
                "GET /app.")
    if actor == "oauth-app":
        return ("not-this-note-oauth-app",
                "the message names an OAuth App, so the organization is "
                "restricting the App rather than the token lacking a "
                "permission.")
    if code == 404:
        return ("ambiguous-404",
                "a 404 rather than a 403, which GitHub uses to avoid "
                "confirming that a private resource exists.")
    if actor == "fine-grained-pat" and org_only:
        return ("org-resource-refused",
                "every repository probe passed and only organization resources "
                "were refused, which is more often a pending approval or an "
                "organization token policy than a missing permission.")
    if actor == "fine-grained-pat":
        wanted = parse_accepted_permissions(
            (headers or {}).get("x-accepted-github-permissions", ""))
        named = " or ".join(
            ", ".join("%s=%s" % pair for pair in alternative)
            for alternative in wanted) or "nothing the response named"
        return ("fine-grained-permission-missing",
                "the endpoint accepts %s and this token does not hold it." % named)
    if not kind.startswith("fine-grained"):
        return ("not-this-note-classic",
                "this credential carries scopes rather than fine-grained "
                "permissions, so the two scope headers answer it directly.")
    return ("unclassified",
            "a refusal whose message names no actor. Log it verbatim rather "
            "than guessing which credential was blamed.")


def graphql_pat_refusals(body):
    """Errors in a GraphQL response that blame the personal access token. Pure.

    Returns a list of (path, message). The response carries no
    x-accepted-github-permissions header at all, so this identifies the field
    that was refused and nothing about the permission it wanted.
    """
    if not isinstance(body, dict):
        return []
    out = []
    for err in body.get("errors") or []:
        if not isinstance(err, dict):
            continue
        if actor_from_message(err.get("message")) == "fine-grained-pat":
            path = ".".join(str(p) for p in (err.get("path") or [])) or "(no path)"
            out.append((path, str(err.get("message") or "")))
    return out


def where_the_requirement_lives(protocol):
    """Where to read what the endpoint wanted, per API. Pure."""
    if str(protocol).lower() == "graphql":
        return ("nowhere on this response. GraphQL refusals carry no "
                "x-accepted-github-permissions header, so make the equivalent "
                "REST call and read it off that refusal instead.")
    return ("the x-accepted-github-permissions header on the refusing "
            "response itself.")


def missing_permissions(headers, grants):
    """Permissions the endpoint named that the probes show are not held. Pure."""
    wanted = parse_accepted_permissions(
        (headers or {}).get("x-accepted-github-permissions", ""))
    missing = []
    for alternative in wanted:
        for name, level in alternative:
            if grants.get(name) in ("refused", None):
                missing.append((name, level))
    return missing


def repair(state, headers=None):
    """The sentence a reader has to act on. Pure."""
    if state == "fine-grained-permission-missing":
        wanted = parse_accepted_permissions(
            (headers or {}).get("x-accepted-github-permissions", ""))
        named = ", ".join("%s=%s" % pair
                          for alternative in wanted for pair in alternative)
        return ("add %s to this token's repository permissions -- exactly what "
                "x-accepted-github-permissions named, and nothing else."
                % (named or "the permission the header names"))
    if state == "org-resource-refused":
        return ("check whether an organization owner still has to approve this "
                "token, and whether the organization allows fine-grained "
                "tokens at all. No permission you tick takes effect first.")
    if state == "not-this-note-app":
        return ("see /github/app-permission-missing/ -- an App's permissions "
                "are readable and adding one needs every installation to "
                "accept the upgrade.")
    if state == "not-this-note-classic":
        return ("see /github/missing-oauth-scope/ -- both halves of that diff "
                "arrive as headers on the same response.")
    if state == "ambiguous-404":
        return ("see /github/404-masking-403/ -- decide between missing and "
                "invisible before changing any permission.")
    if state == "clean":
        return ("nothing on this call. Run the probe matrix anyway if you want "
                "to know what the token can reach before it matters.")
    return ("record the status, the message and the "
            "x-accepted-github-permissions header verbatim; between them they "
            "name the actor and the requirement.")


def run_query(session, document, variables):
    """Send one query. Returns (status, body-or-None).

    A GraphQL query is a read; POST is only how the document reaches the
    endpoint, which is why the verb is written here beside the URL rather than
    hidden in a constant where it could be mistaken for a write path.
    """
    r = session.post(API + "/graphql",
                     json={"query": document, "variables": variables or {}},
                     timeout=30)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


def message_of(response):
    """The message field of an error body, or an empty string."""
    try:
        return str((response.json() or {}).get("message") or "")
    except ValueError:
        return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="owner/name to probe")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (the token you are diagnosing)")
        return 2
    try:
        owner, repo = args.repo.split("/", 1)
    except ValueError:
        log.error("--repo takes owner/name")
        return 2

    why_not = refusal(ISSUES_QUERY)
    if why_not:
        log.error("refusing to send: %s", why_not)
        return 2

    log.info("cost: %d core request(s) out of 5,000/hour, plus %d GraphQL point",
             len(PROBES) + 1, POINTS_PER_QUERY)

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })

    who = session.get(API + "/user", timeout=30)
    kind, detail = identify(token, dict(who.headers))
    log.info("credential: %s", kind)
    log.info("  %s", detail)

    grants, rows = {}, []
    refusal_headers, refusal_message, refusal_status = {}, "", 0
    for name, path, label in PROBES:
        r = session.get(API + path.format(owner=owner, repo=repo), timeout=30)
        msg = message_of(r) if r.status_code >= 400 else ""
        verdict, why = grant_from_probe(r.status_code, msg)
        grants[name] = verdict
        accepted = r.headers.get("x-accepted-github-permissions", "")
        if verdict == "refused" and not refusal_message:
            refusal_headers = dict(r.headers)
            refusal_message, refusal_status = msg, r.status_code
        log.info("%-14s %s  %-10s %s", name, r.status_code, verdict,
                 ("x-accepted-github-permissions: " + accepted) if accepted else why)
        rows.append({"permission": name, "settings_label": label,
                     "status": r.status_code, "verdict": verdict,
                     "accepted": accepted})

    state, why = classify(refusal_status, refusal_message, refusal_headers, token)
    log.info("%s: %s", state, why)
    log.info("the requirement lives in %s", where_the_requirement_lives("rest"))

    status, body = run_query(session, ISSUES_QUERY, {"owner": owner, "name": repo})
    gql = graphql_pat_refusals(body)
    log.info("graphql: HTTP %s, %d refusal(s) naming the personal access token",
             status, len(gql))
    for path, msg in gql:
        log.info("  path=%s  %s", path, msg)
    if gql:
        log.info("through graphql the requirement lives %s",
                 where_the_requirement_lives("graphql"))

    log.info("repair: %s", repair(state, refusal_headers))

    print(json.dumps({
        "credential": kind,
        "prefix": token_prefix(token),
        "scope_header": scope_header_state(dict(who.headers)),
        "probes": rows,
        "missing_permissions": missing_permissions(refusal_headers, grants),
        "graphql_refusals": gql,
        "state": state,
        "detail": why,
        "repair": repair(state, refusal_headers),
    }, indent=2, default=str))
    return 1 if state.startswith(("fine-grained", "org-resource")) else 0


if __name__ == "__main__":
    sys.exit(main())
