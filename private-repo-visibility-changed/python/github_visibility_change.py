"""Tell a repository that went private apart from one that was deleted.

Read only. GET requests and nothing else, and one of them carries no credential
at all: the method here is a comparison between two callers reading the same
URL, so the anonymous session is built on purpose and kept separate from the
authenticated one.

The point of the note: making a repository private removes anonymous access
entirely, and GitHub answers 404 rather than 403 so that error codes cannot be
used to enumerate resources. A client that read the repository anonymously for
years therefore sees exactly what it would see if the repository had been
deleted. One reading cannot separate those. Two can.

What this can and cannot see: there is no visibility-changed timestamp on the
repository object. updated_at moves for unrelated reasons and the audit log
that records the change needs organization-level access. So this reports the
current asymmetry and never a date it cannot read.

Environment:

    GITHUB_TOKEN    a token that still has access to the repository
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_visibility_change")

API = "https://api.github.com"
UA = "github-visibility-change/1.0"

# The unauthenticated core quota, per IP address. Reading this number is how a
# client proves whether it is authenticated at all, and the read is free.
ANONYMOUS_CORE_LIMIT = 60

# The three values of `visibility`. `private` the boolean is true for two of
# them, which is why a client keying on the boolean cannot tell them apart.
VISIBILITIES = ("public", "private", "internal")

# The classic scope that describes exactly what has stopped being true.
BLIND_SCOPE = "public_repo"
PRIVATE_SCOPE = "repo"

# What a fine-grained token needs instead, on that one repository.
FINE_GRAINED_PERMISSIONS = ("Metadata: Read", "Contents: Read")


def read_cost():
    """Billable requests this run spends against the core quota. Pure.

    Two repository reads. The two /rate_limit calls are free and one of the
    repository reads is billed to the unauthenticated bucket for this IP
    address rather than to the token.
    """
    return 2


def client_is_anonymous(core_limit):
    """Was this caller authenticated. Pure.

    A limit of 60 is the unauthenticated bucket. An expired or revoked token
    authenticates as nobody and produces the same number, which is why this is
    a better question than "is a token set in the environment".
    """
    try:
        return int(core_limit) <= ANONYMOUS_CORE_LIMIT
    except (TypeError, ValueError):
        return None


def visibility_of(repo):
    """The three-valued visibility, falling back to the boolean. Pure."""
    repo = repo or {}
    value = str(repo.get("visibility") or "").strip().lower()
    if value in VISIBILITIES:
        return value
    if repo.get("private") is True:
        return "private"
    if repo.get("private") is False:
        return "public"
    return "unreported"


def scope_list(header_value):
    """Read x-oauth-scopes into a list, keeping absent and empty apart. Pure."""
    if header_value is None:
        return None
    return [s.strip() for s in header_value.split(",") if s.strip()]


def scope_gap(scopes, visibility):
    """Is the client's scope set exactly the wrong shape for this. Pure.

    Returns (state, detail). public_repo is the interesting one: it grants
    every public repository and no private one, so it is precisely as useful
    as no token at all once the repository stops being public.
    """
    if visibility == "public":
        return ("not-applicable",
                "the repository is public, so no scope is required to read it.")
    if scopes is None:
        return ("no-scopes-reported",
                "this credential reports no OAuth scopes, so it is a "
                "fine-grained or App token. It needs %s on this repository, "
                "granted by the owner." % ", ".join(FINE_GRAINED_PERMISSIONS))
    if PRIVATE_SCOPE in scopes:
        return ("scope-sufficient",
                "the token carries '%s', which covers a private repository. If "
                "it still cannot read this one, the account behind it has no "
                "grant on the repository." % PRIVATE_SCOPE)
    if BLIND_SCOPE in scopes:
        return ("blind-scope",
                "the token carries '%s' and not '%s'. That scope grants every "
                "public repository and no private one, so it is exactly as "
                "blind here as sending no token at all."
                % (BLIND_SCOPE, PRIVATE_SCOPE))
    return ("scope-insufficient",
            "the token carries %s, none of which reaches a private repository. "
            "It needs '%s'." % (", ".join(scopes) or "no scopes at all",
                                PRIVATE_SCOPE))


def classify(anon_status, auth_status, repo=None):
    """Sort a pair of readings of one URL. Pure. Returns (state, detail).

    The ambiguous combinations are named as ambiguous. Resolving them here
    would be the same mistake the 404 makes, committed on purpose.
    """
    visibility = visibility_of(repo)
    if str(anon_status) == "301" or str(auth_status) == "301":
        return ("moved",
                "a 301 means the repository was renamed or transferred and a "
                "redirect was left behind. That is a different note; follow it "
                "once and rewrite your configuration.")
    if auth_status == 200 and anon_status == 404:
        if visibility == "internal":
            return ("internal-visibility",
                    "the repository is internal: private=true, but readable by "
                    "every member of the enterprise rather than by a named list. "
                    "A client keying on the private boolean cannot see that "
                    "difference, and the repair for it is membership rather "
                    "than a repository grant.")
        return ("went-private",
                "the repository is readable with a token and invisible without "
                "one, so it exists and is no longer public. Deletion would "
                "answer 404 to both readings.")
    if auth_status == 200 and anon_status == 200:
        return ("still-public",
                "both readings succeeded, so visibility is not what broke. The "
                "404 your client recorded has another cause.")
    if auth_status == 404 and anon_status == 404:
        return ("invisible-to-both",
                "neither reading can see it, so this is deletion or an account "
                "that was never granted access. That is the wider 404 triage "
                "and not this note.")
    if auth_status != 200 and anon_status == 200:
        return ("token-is-the-problem",
                "the anonymous read succeeded and the authenticated one did "
                "not, so the repository is public and the credential is "
                "failing. Check whether the token is expired or revoked.")
    return ("unclassified",
            "authenticated %s and anonymous %s is not a combination this sorts. "
            "Report both codes before drawing a conclusion."
            % (auth_status, anon_status))


def fork_fallout(repo):
    """The second, slower failure this change produces. Pure, or None.

    Forks that existed while the repository was public are split into their own
    network and stay public. Something that looks like the repository therefore
    still resolves, and a tool that follows it starts tracking a copy that no
    longer receives commits.
    """
    repo = repo or {}
    if visibility_of(repo) == "public":
        return None
    if (repo.get("forks_count") or 0) <= 0:
        return None
    return ("forks that existed while it was public were split into their own "
            "network and are still public, so a link that still resolves may be "
            "a copy that stopped receiving commits.")


def blind_spot():
    """What this cannot establish, said out loud. Pure."""
    return ("no visibility-changed timestamp is exposed to a reader, and the "
            "audit log that records it needs organization-level access. When it "
            "happened is in your own logs, not in this response.")


def repair(state, scope_state=None):
    """The sentence a reader has to act on. Pure."""
    credential = ("give the client the '%s' scope (classic) or %s on this "
                  "repository (fine-grained), granted by the owner."
                  % (PRIVATE_SCOPE, " and ".join(FINE_GRAINED_PERMISSIONS)))
    if state == "went-private":
        text = credential
        if scope_state == "blind-scope":
            text += (" The scope it holds now, '%s', covers exactly what has "
                     "stopped being true." % BLIND_SCOPE)
        return text
    if state == "internal-visibility":
        return ("the repository is internal, so access follows enterprise "
                "membership. A machine account has to be a member of the "
                "enterprise, and after that " + credential)
    if state == "invisible-to-both":
        return ("stop here and run the wider 404 triage. Nothing about "
                "visibility can be established when no credential can see it.")
    if state == "still-public":
        return ("look elsewhere. The repository is public and readable "
                "anonymously, so the 404 came from something other than "
                "visibility.")
    if state == "token-is-the-problem":
        return ("check the credential rather than the repository. An expired "
                "or revoked token authenticates as nobody.")
    if state == "moved":
        return ("follow the redirect once, take full_name from the response, "
                "and store the repository id so the next rename is not a "
                "surprise either.")
    return "report both status codes before drawing a conclusion."


def core_limit(session):
    """core.limit for whichever caller this session is. Free to read."""
    r = session.get(API + "/rate_limit", timeout=30)
    if r.status_code != 200:
        return None
    try:
        return ((r.json().get("resources") or {}).get("core") or {}).get("limit")
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo", help="owner/name of the repository that started 404ing")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token with access is enough)")
        return 2
    if "/" not in args.repo:
        log.error("repo should be owner/name")
        return 2

    log.info("read cost: %d request(s) against the core hourly quota, plus 2 "
             "free /rate_limit calls. One of the reads is anonymous and is "
             "billed to the unauthenticated bucket for this IP address, which "
             "is %d an hour.", read_cost(), ANONYMOUS_CORE_LIMIT)

    common = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    }
    authed = requests.Session()
    authed.headers.update(dict(common, Authorization="Bearer " + token))
    # Deliberately credential-free. The whole method is a comparison between
    # two callers, so this session must never acquire an Authorization header.
    anon = requests.Session()
    anon.headers.update(common)

    auth_limit = core_limit(authed)
    anon_limit = core_limit(anon)

    auth_response = authed.get(API + "/repos/" + args.repo, timeout=30,
                               allow_redirects=False)
    anon_response = anon.get(API + "/repos/" + args.repo, timeout=30,
                             allow_redirects=False)
    log.info("authenticated: HTTP %s  core.limit=%s", auth_response.status_code,
             auth_limit)
    log.info("anonymous:     HTTP %s  core.limit=%s", anon_response.status_code,
             anon_limit)
    if client_is_anonymous(auth_limit):
        log.warning("the authenticated session reports the unauthenticated "
                    "limit, so GITHUB_TOKEN is expired, revoked or not being "
                    "sent. Fix that before reading anything else here.")

    repo = None
    scopes = scope_list(auth_response.headers.get("x-oauth-scopes"))
    if auth_response.status_code == 200:
        repo = auth_response.json()
        log.info("%s: private=%s visibility=%s", args.repo, repo.get("private"),
                 visibility_of(repo))

    state, detail = classify(anon_response.status_code,
                             auth_response.status_code, repo)
    log.info("%s: %s", state, detail)
    scope_state, scope_detail = scope_gap(scopes, visibility_of(repo))
    log.info("%s: %s", scope_state, scope_detail)
    fallout = fork_fallout(repo)
    if fallout:
        log.info("forks-note: %s", fallout)
    log.info("blind-spot: %s", blind_spot())
    log.info("repair: %s", repair(state, scope_state))

    print(json.dumps({
        "repository": args.repo,
        "authenticated_status": auth_response.status_code,
        "anonymous_status": anon_response.status_code,
        "authenticated_core_limit": auth_limit,
        "anonymous_core_limit": anon_limit,
        "client_was_anonymous": client_is_anonymous(auth_limit),
        "private": (repo or {}).get("private"),
        "visibility": visibility_of(repo),
        "scopes": scopes,
        "state": state,
        "detail": detail,
        "scope_state": scope_state,
        "forks_note": fallout,
        "blind_spot": blind_spot(),
        "repair": repair(state, scope_state),
    }, indent=2, default=str))
    return 1 if state in ("went-private", "internal-visibility") else 0


if __name__ == "__main__":
    sys.exit(main())
