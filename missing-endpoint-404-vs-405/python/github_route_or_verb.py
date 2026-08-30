"""Tell a route that does not exist from one that refuses your verb.

Read only, and pointedly so. GitHub answers 404 rather than 405 for an
unsupported method, and the tempting way to settle that is to send the method
and see. This script will not: several of the verbs involved perform the
operation on success, and an unsupported one returns the same 404 you already
have, so the experiment is a production change that buys no information.

The evidence is a GET on the same path, the documentation_url in the error
body, the shape of the path itself, and a table of documented verbs.

Environment:

    GITHUB_TOKEN    optional. A read-only token widens what the GET can see;
                    without one, private paths answer 404 for a third reason.
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_route_or_verb")

API = "https://api.github.com"
UA = "github-route-or-verb/1.0"

# The bare REST index. GitHub degrades documentation_url to exactly this when
# nothing was routed, and names a specific endpoint when a handler ran.
DOCS_INDEX = "https://docs.github.com/rest"

# Verbs are held lowercase throughout and upper-cased only for display. That
# is not cosmetic: it keeps this file free of the literals a write would need,
# so the read-only guard on this section cannot be satisfied by accident.
SAFE_VERBS = ("get", "head")

# Routes people habitually call with the wrong verb, from the REST docs. Not
# an index of the API and not meant to be: a path that is not here produces
# "route-not-in-table", never a guess.
ROUTE_TABLE = (
    ("/user/starred/{owner}/{repo}", ("get", "put", "delete"),
     "check, star, unstar. Starring is a set operation, so it is PUT."),
    ("/user/following/{username}", ("get", "put", "delete"),
     "check, follow, unfollow."),
    ("/gists/{gist_id}/star", ("get", "put", "delete"),
     "check, star, unstar."),
    ("/repos/{owner}/{repo}", ("get", "patch", "delete"),
     "read, update, delete. Updating a repository is PATCH, not PUT."),
    ("/repos/{owner}/{repo}/topics", ("get", "put"),
     "read and replace. There is no POST: the whole list is set at once."),
    ("/repos/{owner}/{repo}/merges", ("post",),
     "creation only. There is no GET here, so a GET probe cannot prove this "
     "route exists."),
    ("/repos/{owner}/{repo}/subscription", ("get", "put", "delete"),
     "read, set, delete a watch."),
    ("/repos/{owner}/{repo}/collaborators/{username}", ("get", "put", "delete"),
     "check, invite, remove. Adding a collaborator is PUT."),
    ("/repos/{owner}/{repo}/branches/{branch}/protection", ("get", "put", "delete"),
     "read, replace, remove."),
    ("/repos/{owner}/{repo}/pulls/{pull_number}", ("get", "patch"),
     "read and update. Updating a pull request is PATCH."),
    ("/repos/{owner}/{repo}/pulls/{pull_number}/merge", ("get", "put"),
     "check whether merged, and merge."),
    ("/repos/{owner}/{repo}/issues", ("get", "post"),
     "list and create."),
    ("/repos/{owner}/{repo}/issues/{issue_number}/labels",
     ("get", "post", "put", "delete"),
     "list, add, replace, remove all. POST adds; PUT replaces the set."),
    ("/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches", ("post",),
     "creation only, and it has no GET."),
    ("/orgs/{org}/memberships/{username}", ("get", "put", "delete"),
     "read, set, remove a membership."),
)


def read_cost(with_root):
    """REST requests this run will spend. Pure. Printed before any are spent."""
    return 2 if with_root else 1


def probe_refusal(verb):
    """Would this script send that verb to find out. Pure. (state, detail).

    A function rather than a comment because it is the load-bearing rule of the
    note, and because both halves of the reasoning have to survive somebody
    reading only the code: the request is a write, and its answer would be
    worthless even if it were free.
    """
    name = str(verb or "").strip().lower()
    if name in SAFE_VERBS:
        return ("safe-to-send",
                "%s does not change anything, so the probe is a reading."
                % name.upper())
    return ("will-not-probe",
            "sending %s to confirm would be a write, and several routes here "
            "perform the operation on success. It would also answer nothing: "
            "an unsupported verb returns 404 on this API, which is the status "
            "you already have. The request costs a production change and "
            "returns no information." % name.upper())


def documentation_url_of(body):
    """The documentation_url in an error body, or None. Pure."""
    if not isinstance(body, dict):
        return None
    value = body.get("documentation_url")
    return value if isinstance(value, str) and value else None


def docs_url_kind(url):
    """Bare REST index, or a specific endpoint. Pure. (kind, detail).

    The whole diagnosis turns on this. GitHub names the endpoint when a handler
    ran and degrades to the index when nothing was routed.
    """
    if not url:
        return ("absent",
                "the body carried no documentation_url, so this reading cannot "
                "say whether a handler was reached.")
    trimmed = str(url).rstrip("/")
    if trimmed == DOCS_INDEX:
        return ("generic",
                "documentation_url is the bare REST index, so no handler was "
                "reached for this path and method.")
    if trimmed.startswith(DOCS_INDEX):
        return ("endpoint-specific",
                "documentation_url names a specific endpoint, so the route "
                "matched and the handler answered. The resource is missing or "
                "hidden, which is a different note.")
    return ("unrecognised",
            "documentation_url points somewhere this script does not "
            "recognise; treat it as no evidence rather than as evidence.")


def classify_not_found(status, body):
    """Sort the probe's answer. Pure. (state, detail)."""
    code = int(status or 0)
    if code == 200:
        return ("route-answers-get",
                "the same path answers a GET, so the path shape is right and "
                "nothing is hidden from this credential. A refusal on another "
                "verb is about the verb.")
    if code == 401:
        return ("unauthenticated",
                "the probe was refused for want of a credential, so it cannot "
                "speak to routing. Re-run with a read-only token.")
    if code in (403, 429):
        return ("refused-not-missing",
                "a refusal is not a routing answer. Sort that 403 first; it "
                "has its own notes.")
    if code != 404:
        return ("unexpected-status",
                "HTTP %s is neither a 404 nor a success, so there is nothing "
                "here to sort." % status)
    kind, detail = docs_url_kind(documentation_url_of(body))
    if kind == "endpoint-specific":
        return ("route-matched-resource-missing", detail)
    if kind == "generic":
        return ("nothing-routed-here", detail)
    return ("routing-unknown", detail)


def path_shape_problem(path):
    """Documented shape errors, checked locally. Pure. (state, detail)."""
    value = str(path or "")
    if not value:
        return ("empty-path", "no path was given.")
    if value.startswith("http://") or value.startswith("https://"):
        return ("full-url-not-path",
                "a whole URL was passed where a path was expected, so the "
                "request went somewhere with the host doubled.")
    if not value.startswith("/"):
        return ("no-leading-slash",
                "the path does not begin with a slash, so it will be joined "
                "onto the base URL wrongly.")
    head = value.split("?", 1)[0]
    if "{" in head or "}" in head:
        return ("placeholder-not-substituted",
                "a template placeholder is still in the path. The request is "
                "asking for a repository literally named with braces.")
    if "//" in head[1:]:
        return ("doubled-slash",
                "the path contains an empty segment, usually an interpolated "
                "value that was empty. That is a different route from the one "
                "you meant.")
    if head != "/" and head.endswith("/"):
        return ("trailing-slash",
                "a trailing slash makes this a different path, and GitHub "
                "documents it as a cause of 404. It is invisible in review.")
    if " " in head:
        return ("unencoded-space",
                "an unencoded space in the path. URL-encode path parameters "
                "before interpolating them.")
    if "\\" in head:
        return ("backslash-in-path",
                "a backslash in the path, usually a Windows path separator "
                "that leaked into a URL.")
    return ("clean",
            "no trailing slash, no unsubstituted placeholder, no unencoded "
            "path parameter.")


def match_route(path):
    """Match a concrete path against the table. Pure. (template, verbs, note).

    Segment-wise, so a placeholder matches exactly one segment. A parameter
    that smuggled a slash into itself therefore fails to match, which is the
    right answer: it really is a different route.
    """
    head = str(path or "").split("?", 1)[0]
    parts = [p for p in head.split("/") if p != ""]
    for template, verbs, note in ROUTE_TABLE:
        wanted = [p for p in template.split("/") if p != ""]
        if len(wanted) != len(parts):
            continue
        ok = True
        for want, got in zip(wanted, parts):
            if want.startswith("{") and want.endswith("}"):
                continue
            if want != got:
                ok = False
                break
        if ok:
            return (template, verbs, note)
    return (None, (), "")


def verb_verdict(path, verb):
    """Is the verb documented for the route this path matches. Pure."""
    name = str(verb or "").strip().lower()
    template, verbs, note = match_route(path)
    if template is None:
        return ("route-not-in-table",
                "this path matches no route in the table, which is a short "
                "list rather than an index of the API. Look the endpoint up "
                "and compare the verb by hand.")
    if name in verbs:
        return ("verb-is-documented",
                "%s is a documented verb for %s (%s), so the method is not "
                "your problem. %s"
                % (name.upper(), template,
                   ", ".join(v.upper() for v in verbs), note))
    return ("verb-not-on-this-route",
            "you sent %s. %s accepts %s. %s"
            % (name.upper(), template,
               ", ".join(v.upper() for v in verbs), note))


def get_probe_is_evidence(path):
    """Can a GET prove this route exists. Pure. (state, detail).

    The honest limit of the whole method. A route with no GET representation
    answers the same bare 404 as a route that does not exist.
    """
    template, verbs, _ = match_route(path)
    if template is None:
        return ("unknown-route",
                "the route is not in the table, so whether a GET would prove "
                "anything is unknown.")
    if "get" in verbs:
        return ("probe-decides",
                "%s has a documented GET, so a 200 from the probe settles the "
                "path shape." % template)
    return ("probe-cannot-decide",
            "%s has no documented GET, so a bare 404 from the probe is "
            "expected and proves nothing. The table is the only evidence "
            "here." % template)


def permissions_header_hint(headers):
    """Weak corroboration from x-accepted-github-permissions. Pure."""
    bag = headers if isinstance(headers, dict) else {}
    for key in bag:
        if str(key).lower() == "x-accepted-github-permissions":
            return ("permissions-were-evaluated",
                    "the response names an accepted permission, which means a "
                    "handler looked at your credential. That points away from "
                    "a routing problem. Corroboration only.")
    return ("no-permission-header",
            "no accepted-permission header came back. That is consistent with "
            "nothing being routed and is far too weak to conclude it alone.")


def root_map_covers(root, path):
    """Does the root endpoint map mention this path family. Pure.

    Deliberately coarse. The root map lists about thirty templates, so a miss
    means very little; a hit confirms the first segment is a real family.
    """
    if not isinstance(root, dict) or not root:
        return ("root-unread",
                "the root endpoint map was not read, so nothing corroborates "
                "the path family.")
    head = str(path or "").split("?", 1)[0]
    parts = [p for p in head.split("/") if p != ""]
    if not parts:
        return ("no-path", "there is no path to check against the map.")
    needle = "/" + parts[0]
    for value in root.values():
        if isinstance(value, str) and needle in value:
            return ("family-known",
                    "the root endpoint map contains %s, so the first segment "
                    "is a real family." % needle)
    return ("family-not-in-map",
            "the root endpoint map does not mention %s. The map covers about "
            "thirty families out of the whole API, so this is a hint and not "
            "a finding." % needle)


def verdict(routing_state, shape_state, verb_state):
    """The finding, in one state. Pure. (state, detail)."""
    if routing_state == "route-matched-resource-missing":
        return ("resource-not-routing",
                "the route matched and the handler answered. This is about "
                "what your credential may see, or about a resource that is "
                "not there, and neither is a method problem.")
    if routing_state in ("unauthenticated", "refused-not-missing",
                         "unexpected-status"):
        return (routing_state,
                "the probe did not produce a routing answer, so nothing can "
                "be concluded about the verb from it.")
    if shape_state != "clean":
        return ("path-shape-wrong",
                "the path itself is malformed, and that is a documented cause "
                "of 404 on this API. Fix the shape before looking at verbs.")
    if verb_state == "verb-not-on-this-route":
        return ("wrong-verb",
                "the path is well formed and matches a documented route that "
                "does not accept the verb you sent. That is the 404.")
    if routing_state == "route-answers-get" and verb_state == "verb-is-documented":
        return ("route-and-verb-both-fine",
                "the path answers a GET and your verb is documented for it, "
                "so the 404 you saw came from somewhere else entirely.")
    if routing_state == "nothing-routed-here" and verb_state == "verb-is-documented":
        return ("route-absent-or-wrong-host",
                "nothing was routed, the path is well formed and the verb is "
                "documented for a route of that shape. Check that you are "
                "talking to the API host you think you are.")
    return ("undetermined",
            "the readings do not settle it. Look the endpoint up and compare "
            "the verb against the documentation by hand.")


def repair(state, path, verb):
    """The sentence a reader has to act on. Pure. Nothing here is sent."""
    template, verbs, _ = match_route(path)
    if state == "wrong-verb":
        return ("send %s to this path instead of %s. Nothing here sends it."
                % (" or ".join(v.upper() for v in verbs if v not in SAFE_VERBS)
                   or "the documented verb", str(verb).upper()))
    if state == "path-shape-wrong":
        return ("fix the path before anything else: URL-encode the parameters, "
                "drop the trailing slash, and substitute every placeholder.")
    if state == "resource-not-routing":
        return ("stop looking at the method. Sort the 404 by what your "
                "credential can see; that has its own note.")
    if state == "route-absent-or-wrong-host":
        return ("confirm the API base URL for this environment. A client "
                "pointed at the wrong GitHub installation 404s every route "
                "that is really there.")
    if state == "unauthenticated":
        return "re-run with a read-only token so the probe means something."
    return ("look the endpoint up in the REST documentation and compare its "
            "verb with the one your client sent. Do not send the verb to find "
            "out.")


def get(session, path):
    """One GET. Returns the response object."""
    return session.get(API + path, timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="the path that 404s, e.g. /repos/o/r/topics")
    parser.add_argument("--verb", default="get",
                        help="the method your failing code sent, e.g. put")
    parser.add_argument("--root", action="store_true",
                        help="also read the root endpoint map for corroboration")
    args = parser.parse_args()

    log.info("read cost: %d REST request(s) against the core hourly quota",
             read_cost(args.root))

    session = requests.Session()
    session.headers.update({
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub refuses requests with no User-Agent before it looks at auth.
        "User-Agent": UA,
    })
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        session.headers["Authorization"] = "Bearer " + token
    else:
        log.warning("no GITHUB_TOKEN: private paths will 404 for a third "
                    "reason and the probe is weaker")

    shape_state, shape_detail = path_shape_problem(args.path)
    log.info("path-shape: %s — %s", shape_state, shape_detail)

    probe = get(session, args.path)
    log.info("probe: GET %s -> HTTP %s", args.path, probe.status_code)
    try:
        body = probe.json()
    except ValueError:
        body = None
    routing_state, routing_detail = classify_not_found(probe.status_code, body)
    log.info("not-found: %s — %s", routing_state, routing_detail)

    hint_state, hint_detail = permissions_header_hint(dict(probe.headers))
    log.info("%s: %s", hint_state, hint_detail)

    evidence_state, evidence_detail = get_probe_is_evidence(args.path)
    log.info("%s: %s", evidence_state, evidence_detail)

    verb_state, verb_detail = verb_verdict(args.path, args.verb)
    log.info("%s: %s", verb_state, verb_detail)

    refusal_state, refusal_detail = probe_refusal(args.verb)
    log.info("%s: %s", refusal_state, refusal_detail)

    root_state, root_detail = ("root-unread", "not read")
    if args.root:
        root = get(session, "/")
        try:
            root_state, root_detail = root_map_covers(root.json(), args.path)
        except ValueError:
            root_state, root_detail = ("root-unread", "the root map did not parse.")
        log.info("%s: %s", root_state, root_detail)

    state, detail = verdict(routing_state, shape_state, verb_state)
    log.info("%s: %s", state, detail)
    fix = repair(state, args.path, args.verb)
    log.info("repair: %s", fix)

    print(json.dumps({
        "path": args.path,
        "verb_sent": str(args.verb).upper(),
        "probe_status": probe.status_code,
        "documentation_url": documentation_url_of(body),
        "routing_state": routing_state,
        "path_shape_state": shape_state,
        "verb_state": verb_state,
        "verb_detail": verb_detail,
        "get_probe_evidence": evidence_state,
        "permission_header_hint": hint_state,
        "root_map_state": root_state,
        "probe_refusal": refusal_state,
        "probe_refusal_detail": refusal_detail,
        "state": state,
        "detail": detail,
        "repair": fix,
    }, indent=2, default=str))
    return 1 if state in ("wrong-verb", "path-shape-wrong",
                          "route-absent-or-wrong-host") else 0


if __name__ == "__main__":
    sys.exit(main())
