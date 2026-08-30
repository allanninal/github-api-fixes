"""Say why a working GitHub App installation token is refused by one route.

Read only. Two GETs: one that proves the token is alive, and one that repeats
the call that was already failing. Nothing is minted, accepted, widened or
changed. Where the repair is a different endpoint, it is printed.

Some REST routes are unreachable with a server-to-server installation token
whatever permissions the App holds. GET /user is the famous one: it means "the
user this credential belongs to", and an installation belongs to an account
rather than to a person. No permission opens it, because permission is not the
question being asked.

Credential classes, abbreviated the same way throughout:

    s2s   an installation access token, acting as the App on one account
    u2s   a user access token, acting as a person who authorized the App
    jwt   the App's own JSON Web Token, signed with its private key
    any   any authenticated caller, a personal access token included
    none  no credential at all
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_endpoint_audience")

API = "https://api.github.com"
UA = "github-endpoint-audience/1.0"

# Which credential classes each route template accepts. There is no endpoint
# that returns this, so the table is curated from the published lists rather
# than fetched, and anything absent from it is answered by heuristic with the
# uncertainty stated rather than hidden.
AUDIENCES = {
    "/": {"none", "any", "s2s", "u2s", "jwt"},
    "/meta": {"none", "any", "s2s", "u2s", "jwt"},
    "/versions": {"none", "any", "s2s", "u2s", "jwt"},
    "/rate_limit": {"any", "s2s", "u2s", "jwt"},
    "/app": {"jwt"},
    "/app/installations": {"jwt"},
    "/app/installations/{installation_id}": {"jwt"},
    "/installation/repositories": {"s2s"},
    "/user": {"any", "u2s"},
    "/user/repos": {"any", "u2s"},
    "/user/emails": {"any", "u2s"},
    "/user/orgs": {"any", "u2s"},
    "/user/keys": {"any", "u2s"},
    "/user/installations": {"any", "u2s"},
    "/notifications": {"any", "u2s"},
    "/gists": {"any", "u2s"},
    "/users/{username}": {"none", "any", "s2s", "u2s"},
    "/orgs/{org}": {"any", "s2s", "u2s"},
    "/orgs/{org}/repos": {"any", "s2s", "u2s"},
    "/orgs/{org}/members": {"any", "s2s", "u2s"},
    "/repos/{owner}/{repo}": {"any", "s2s", "u2s"},
    "/repos/{owner}/{repo}/issues": {"any", "s2s", "u2s"},
    "/repos/{owner}/{repo}/pulls": {"any", "s2s", "u2s"},
    "/repos/{owner}/{repo}/hooks": {"any", "s2s", "u2s"},
    "/repos/{owner}/{repo}/commits": {"any", "s2s", "u2s"},
    "/search/issues": {"any", "s2s", "u2s"},
}

# What to call instead. None for the second element means there is no
# server-to-server equivalent, which is a real answer and a better one than a
# nearby endpoint that returns different data.
SUBSTITUTES = {
    "/user": ("/app",
              "identifies the App itself, and is called with the App JWT "
              "rather than with the installation token"),
    "/user/repos": ("/installation/repositories",
                    "returns exactly the repositories this installation "
                    "covers, which is narrower and more accurate"),
    "/user/installations": ("/app/installations",
                            "lists the installations of this App, under the "
                            "App JWT"),
    "/user/orgs": ("/app/installations",
                   "each installation names the account it sits on, which is "
                   "the App equivalent of asking which orgs you are in"),
    "/user/emails": (None,
                     "email addresses belong to a person; only a user access "
                     "token can read them"),
    "/user/keys": (None,
                   "SSH keys belong to a person; only a user access token can "
                   "read them"),
    "/notifications": (None,
                       "notifications belong to a person; subscribe the App to "
                       "webhook events instead of polling a human inbox"),
    "/gists": (None, "GitHub Apps cannot reach gists at all"),
    "/app": (None,
             "this route is right, but it wants the App JWT; the installation "
             "token is the thing the JWT produces, not a substitute for it"),
    "/app/installations": (None,
                           "this route wants the App JWT, not the installation "
                           "token it produces"),
}

# Placeholder-free templates are matched first, so /user/repos wins over a
# same-shaped template with a variable in the first position.
ROUTES = sorted(AUDIENCES, key=lambda t: (t.count("{"), t))


def canonical(path):
    """Reduce a request path to the route template it matches. Pure.

    A full URL, a query string, a fragment and a trailing slash all have to
    land on the same template, because the path in a log line is rarely the
    tidy form. Returns None when nothing matches, which the caller reports as
    uncertainty rather than treating as a permitted route.
    """
    raw = str(path or "").split("?")[0].split("#")[0].strip()
    for prefix in ("https://api.github.com", "http://api.github.com"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
    if not raw.startswith("/"):
        raw = "/" + raw
    parts = [p for p in raw.split("/") if p]
    if not parts:
        return "/"
    for template in ROUTES:
        segments = [p for p in template.split("/") if p]
        if len(segments) != len(parts):
            continue
        if all(s.startswith("{") or s == got for s, got in zip(segments, parts)):
            return template
    return None


def accepts(route):
    """The credential classes a known route template accepts. Pure."""
    found = AUDIENCES.get(route)
    return set(found) if found else None


def guess(path):
    """Heuristic audience for a path the table has never seen. Pure.

    Returns (classes, reason). classes is None where even the heuristic
    declines, because a guessed answer in this particular report would send
    somebody to rewrite a working call.
    """
    parts = [p for p in str(path or "").split("?")[0].split("/") if p]
    if not parts:
        return None, "an empty path matches nothing"
    head = parts[0]
    if head == "user":
        return ({"any", "u2s"},
                "every route under /user means the authenticated user, and an "
                "installation is not a user")
    if head == "app":
        return ({"jwt"},
                "routes under /app identify the App and are signed with the "
                "App JWT rather than an installation token")
    if head == "installation":
        return ({"s2s"},
                "routes under /installation are the installation's own view "
                "of itself")
    if head in ("notifications", "gists"):
        return ({"any", "u2s"},
                "this resource belongs to a person, so it needs a credential "
                "that has one behind it")
    return None, ("not in the table, and the first path segment carries no "
                  "rule this script is willing to apply")


def substitute(route):
    """The App-appropriate replacement for a route, if there is one. Pure."""
    return SUBSTITUTES.get(route)


def verdict(alive, status, route, classes, guessed=False):
    """Turn a liveness proof and a route lookup into a finding. Pure.

    alive is whether GET /installation/repositories returned 200, which is the
    only thing that distinguishes "the credential is broken" from "the route
    refuses this class of credential". Without it the two are the same 403.
    """
    if not alive:
        return ("not-an-installation-token",
                "GET /installation/repositories did not return 200, so this "
                "credential is not a live installation access token. Whatever "
                "the other call did, it is not the mismatch this script looks "
                "for.")
    if status is not None and status < 400:
        return ("endpoint-accepted",
                "%s returned %d with this installation token, so the route "
                "accepts it." % (route or "that path", status))
    if classes is None:
        return ("route-unknown",
                "this path is not in the route table and the heuristic "
                "declined it, so the audience is genuinely unknown. Check the "
                "published list of endpoints available to installation access "
                "tokens before rewriting anything.")

    hedge = " (by heuristic rather than from the table)" if guessed else ""
    if "s2s" in classes:
        return ("installation-tokens-accepted",
                "this route does accept installation access tokens%s, so the "
                "refusal is about a permission rather than about the "
                "credential class. Read x-accepted-github-permissions on the "
                "same response." % hedge)
    if "jwt" in classes and "u2s" not in classes:
        return ("needs-app-jwt",
                "this route wants the App's own JWT%s. The installation token "
                "is what the JWT produces, not a substitute for it: sign a "
                "fresh JWT and send that instead." % hedge)
    return ("needs-user-context",
            "this route accepts %s%s. An installation access token is not one "
            "of them, so no permission opens it: the credential has no user "
            "behind it and the route is asking about one."
            % (", ".join(sorted(classes)), hedge))


def get(session, path):
    """One GET. Returns (status, json-or-None)."""
    url = API + path if path.startswith("/") else path
    r = session.get(url, timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default="/user",
                    help="the API path that was refused, taken verbatim from "
                         "the failing log line")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_INSTALLATION_TOKEN") or \
        os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_INSTALLATION_TOKEN to an installation access "
                  "token. Without one there is no credential class to test a "
                  "route against")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    # The liveness proof, and the only request here that is not the reader's
    # own call. Only an installation access token can answer it at all.
    probe_status, probe_body = get(session, "/installation/repositories?per_page=1")
    alive = probe_status == 200
    if alive:
        total = probe_body.get("total_count") if isinstance(probe_body, dict) else None
        log.info("installation token alive: GET /installation/repositories "
                 "returned 200 over %s repositories",
                 total if total is not None else "an unreported number of")
    else:
        log.info("GET /installation/repositories returned %d, so this is not "
                 "a live installation access token", probe_status)

    status = None
    if alive:
        status, _ = get(session, args.path)
        log.info("%s returned %d", args.path, status)

    route = canonical(args.path)
    classes = accepts(route) if route else None
    guessed = False
    if classes is None:
        classes, reason = guess(args.path)
        guessed = classes is not None
        log.info("route: %s", route or "not in the table (%s)" % reason)
    else:
        log.info("route: %s accepts %s", route, ", ".join(sorted(classes)))

    state, detail = verdict(alive, status, route, classes, guessed)
    log.info("%s: %s", state, detail)

    if state in ("needs-user-context", "needs-app-jwt"):
        swap = substitute(route)
        if swap and swap[0]:
            log.info("repair: call %s instead, which %s", swap[0], swap[1])
        elif swap:
            log.info("repair: there is no server-to-server equivalent. %s",
                     swap[1])
        else:
            log.info("repair: find the App equivalent of this route in the "
                     "published endpoint list, or authorize a user and hold a "
                     "user access token for them")
    if state == "installation-tokens-accepted":
        log.info("repair: this is a permissions finding rather than a "
                 "credential-class one; diff the App's permissions against "
                 "the header the failing response carried")

    print(json.dumps({"path": args.path, "route": route, "status": status,
                      "installation_token_alive": alive,
                      "accepts": sorted(classes) if classes else None,
                      "by_heuristic": guessed, "state": state}, indent=2))
    return 1 if state in ("needs-user-context", "needs-app-jwt") else 0


if __name__ == "__main__":
    sys.exit(main())
