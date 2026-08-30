"""Name the narrowest scope that would have made a refused GitHub call succeed.

Read only. Both requests are GETs, and one of them is the call you are already
making. Nothing here mints, rotates or revokes anything: the repair is printed
for you to run.

The two headers that matter ride on the same response:

    x-oauth-scopes:          what this token holds
    x-accepted-oauth-scopes: what this endpoint accepts, as alternatives

Scope satisfaction is not set membership. The accepted list is a disjunction,
and held scopes imply narrower ones, so the diff has to be computed rather than
eyeballed.
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_scope_diff")

API = "https://api.github.com"
UA = "github-scope-diff/1.0"

# Holding the key already grants everything in the value, transitively. Only the
# implications that change a diff are listed; a scope absent from this table
# implies nothing, which is the common case.
IMPLIES = {
    "repo": ["public_repo", "repo:status", "repo_deployment", "repo:invite",
             "security_events"],
    "admin:org": ["write:org"],
    "write:org": ["read:org"],
    "admin:repo_hook": ["write:repo_hook"],
    "write:repo_hook": ["read:repo_hook"],
    "admin:org_hook": [],
    "admin:public_key": ["write:public_key"],
    "write:public_key": ["read:public_key"],
    "admin:gpg_key": ["write:gpg_key"],
    "write:gpg_key": ["read:gpg_key"],
    "user": ["read:user", "user:email", "user:follow"],
    "write:packages": ["read:packages"],
    "write:discussion": ["read:discussion"],
    "project": ["read:project"],
}

# Lower is narrower. Used only to break ties between alternatives that would
# both work, so the report recommends public_repo over repo rather than the one
# that happened to be listed first.
RANK = {
    "read:org": 10, "read:user": 10, "read:packages": 10, "read:project": 10,
    "read:discussion": 10, "read:repo_hook": 10, "repo:status": 12,
    "user:email": 12, "repo_deployment": 15, "security_events": 18,
    "public_repo": 20, "write:org": 30, "write:repo_hook": 30,
    "write:packages": 30, "write:discussion": 30, "gist": 25, "notifications": 25,
    "admin:repo_hook": 40, "admin:org_hook": 45, "workflow": 55, "repo": 60,
    "user": 60, "admin:org": 70, "delete_repo": 80, "delete:packages": 80,
    "site_admin": 95,
}
DEFAULT_RANK = 50


def parse_scopes(value):
    """Parse an x-oauth-scopes header value. Pure.

    Returns None when the header was absent and a list when it was present, so
    "this credential does not use scopes" stays distinguishable from "this
    credential was minted with none". They have different repairs and only one
    of them is on this page.
    """
    if value is None:
        return None
    return [s.strip() for s in str(value).split(",") if s.strip()]


def expand(scopes):
    """Close a held scope set over the implication table. Pure.

    Without this, a token holding repo is reported as missing public_repo, the
    reader tries it, nothing changes, and the report stops being believed.
    """
    seen, queue = set(), list(scopes or [])
    while queue:
        scope = queue.pop()
        if scope in seen:
            continue
        seen.add(scope)
        queue.extend(IMPLIES.get(scope, ()))
    return seen


def alternatives(value):
    """Parse x-accepted-oauth-scopes into alternative requirement sets. Pure.

    Commas separate alternatives, any one of which satisfies the endpoint.
    Whitespace inside one alternative is treated as a conjunction, which is
    defensive rather than common. None means the header was absent; an empty
    list means it was present and empty, which is the endpoint saying it accepts
    any authenticated caller.
    """
    if value is None:
        return None
    out = []
    for item in str(value).split(","):
        parts = sorted({p for p in item.replace(" and ", " ").split() if p})
        if parts:
            out.append(tuple(parts))
    return out


def satisfies(held, accepted):
    """Decide whether held scopes satisfy an accepted list. Pure.

    Returns (ok, options). ok is None when the endpoint named no scopes at all.
    options lists what each unmet alternative is missing, narrowest first, so
    the caller can recommend the cheapest one rather than the first one.
    """
    if accepted is None:
        return None, []
    if not accepted:
        return True, []
    have = expand(held or [])
    options = []
    for alt in accepted:
        missing = tuple(s for s in alt if s not in have)
        if not missing:
            return True, []
        options.append(missing)
    options.sort(key=lambda m: (len(m),
                                sum(RANK.get(s, DEFAULT_RANK) for s in m), m))
    return False, options


def verdict(status, held, accepted):
    """Turn a status code and a header pair into a finding. Pure."""
    if held is None:
        return ("not-a-scoped-credential",
                "the response carried no x-oauth-scopes header, so this is a "
                "fine-grained token, an App installation token or no credential "
                "at all. None of those use scopes; they use per-resource "
                "permissions, and the missing one is named by "
                "x-accepted-github-permissions instead.")
    if status < 400:
        return ("call-succeeded",
                "the call returned %d, so there is nothing to diff. Held: %s"
                % (status, ", ".join(held) or "none"))

    ok, options = satisfies(held, accepted)
    if ok is None:
        return ("endpoint-named-no-scopes",
                "the %d response carried no x-accepted-oauth-scopes header, so "
                "the endpoint did not name a scope. Scope is not the cause "
                "here; look at SSO authorization, App installation coverage or "
                "plain lack of access." % status)
    if ok and not accepted:
        return ("any-token-accepted",
                "x-accepted-oauth-scopes was present and empty, which means the "
                "endpoint accepts any authenticated token. The %d is therefore "
                "not about scopes and no scope will fix it." % status)
    if ok:
        return ("scope-satisfied",
                "the token already satisfies %s, so the %d has another cause. "
                "Held: %s" % (" or ".join("+".join(a) for a in accepted),
                              status, ", ".join(held) or "none"))

    cheapest = options[0]
    return ("missing-scope",
            "add %s (narrowest of %d alternative(s)) and the call succeeds. "
            "Held: %s. Accepted: %s"
            % ("+".join(cheapest), len(options), ", ".join(held) or "none",
               " or ".join("+".join(a) for a in accepted)))


def get(session, path):
    """One GET. Returns (status, json-or-None, lowercased headers)."""
    url = API + path if path.startswith("/") else path
    r = session.get(url, timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body, {k.lower(): v for k, v in r.headers.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default="/user",
                    help="the API path that was refused, for example "
                         "/repos/OWNER/REPO/hooks")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN. An anonymous request carries no "
                  "x-oauth-scopes header at all, so there is nothing to diff")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    # GET /user is the cheapest place to read what the token holds, and it
    # answers even when the failing path 404s without headers.
    base_status, base_body, base_headers = get(session, "/user")
    held = parse_scopes(base_headers.get("x-oauth-scopes"))
    if base_status == 200 and isinstance(base_body, dict):
        log.info("authenticated as %s", base_body.get("login", "an unnamed user"))
    elif base_status == 401:
        log.error("GET /user returned 401, so the credential is rejected "
                  "outright. That is a different problem from a narrow one")
        return 2

    status, _, headers = get(session, args.path)
    # The failing response is the authoritative one for both headers; fall back
    # to the /user reading only where the failure omitted them.
    held = parse_scopes(headers.get("x-oauth-scopes")) or held
    accepted = alternatives(headers.get("x-accepted-oauth-scopes"))
    log.info("%s returned %d", args.path, status)
    log.info("held:     %s", ", ".join(held or []) if held is not None
             else "header absent, not a scoped credential")
    log.info("accepted: %s", headers.get("x-accepted-oauth-scopes",
                                         "header absent"))

    state, detail = verdict(status, held, accepted)
    log.info("%s: %s", state, detail)

    if state == "missing-scope":
        _, options = satisfies(held, accepted)
        want = "+".join(options[0])
        log.info("repair: mint a replacement token that adds %s, deploy it, "
                 "then revoke the old one. Scopes cannot be widened in place.",
                 want)
        log.info("repair: for a gh CLI credential, gh auth refresh -h "
                 "github.com -s %s", options[0][0])
    if state == "not-a-scoped-credential":
        log.info("repair: read x-accepted-github-permissions on the same "
                 "response and add that permission to the App or the "
                 "fine-grained token instead.")

    print(json.dumps({"path": args.path, "status": status, "held": held,
                      "accepted": accepted, "state": state}, indent=2))
    return 1 if state in ("missing-scope", "not-a-scoped-credential") else 0


if __name__ == "__main__":
    sys.exit(main())
