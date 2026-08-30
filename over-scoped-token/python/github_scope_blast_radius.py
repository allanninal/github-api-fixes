"""Inventory what a working GitHub token is allowed to do that it never does.

Read only, and in a stronger sense than usual: the single request is GET /user.
Nothing here probes a write to see whether it would be permitted, because a
probe that is permitted is a write. The repair, a fine-grained permission list,
is printed for you to mint.

Nothing is failing when you run this. That is the point of it.
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_scope_blast_radius")

API = "https://api.github.com"
UA = "github-scope-blast-radius/1.0"

# What each scope authorizes, phrased as the thing it lets somebody do rather
# than as the name of the scope. A report that names verbs gets acted on; a
# report that names scopes gets nodded at. Read-only scopes are absent from this
# table on purpose: holding one unnecessarily is untidy, not dangerous.
CAPABILITIES = {
    "repo": ["push to every public and private repository the account can reach",
             "create and remove branches, tags and releases",
             "change repository settings, collaborators and deploy keys"],
    "public_repo": ["push to every public repository the account can reach"],
    "delete_repo": ["permanently remove any repository the account administers"],
    "admin:org": ["add and remove organization members",
                  "create, rename and dissolve teams"],
    "write:org": ["change team membership and organization projects"],
    "admin:org_hook": ["create, edit and remove organization webhooks"],
    "admin:repo_hook": ["create, edit and remove repository webhooks"],
    "write:repo_hook": ["create and edit repository webhooks"],
    "workflow": ["change workflow files, which run on the next push"],
    "write:packages": ["publish and overwrite package versions"],
    "delete:packages": ["permanently remove published package versions"],
    "gist": ["create and edit gists on the account"],
    "user": ["change the account profile and its email addresses"],
    "admin:public_key": ["add an SSH key to the account"],
    "admin:gpg_key": ["add a signing key to the account"],
    "write:discussion": ["post and edit team discussions"],
    "notifications": ["mark notifications read and manage subscriptions"],
}

# The smallest classic scope that serves each kind of read. An empty list means
# no scope at all is required, which surprises people: public repository data is
# readable by any authenticated caller.
NEEDS_CLASSIC = {
    "public-repos": [],
    "private-repos": ["repo"],
    "pull-requests": ["repo"],
    "issues": ["repo"],
    "actions-runs": ["repo"],
    "org-members": ["read:org"],
    "repo-hooks": ["read:repo_hook"],
    "packages": ["read:packages"],
    "user-profile": ["read:user"],
}

# The same reads expressed as fine-grained permissions, which is the repair.
NEEDS_FINE_GRAINED = {
    "public-repos": ["Metadata: Read"],
    "private-repos": ["Contents: Read", "Metadata: Read"],
    "pull-requests": ["Metadata: Read", "Pull requests: Read"],
    "issues": ["Issues: Read", "Metadata: Read"],
    "actions-runs": ["Actions: Read", "Metadata: Read"],
    "org-members": ["Members: Read (organization)"],
    "repo-hooks": ["Webhooks: Read"],
    "packages": ["Packages: Read"],
    "user-profile": ["Profile: Read (account)"],
}

# Classic scopes that grant write and cannot be avoided for the reads that need
# them. Holding one of these is not a mistake in scope choice; it is the reason
# to stop using classic tokens.
UNAVOIDABLY_BROAD = {"repo", "public_repo"}


def held_scopes(headers):
    """Read x-oauth-scopes and say what kind of credential this is. Pure.

    Returns (scopes, kind). An absent header is the healthy answer here, not a
    missing one: fine-grained tokens and App installation tokens do not carry
    account-wide scopes at all.
    """
    lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
    if "x-oauth-scopes" not in lowered:
        return None, "not-scope-based"
    raw = lowered["x-oauth-scopes"]
    return [s.strip() for s in str(raw).split(",") if s.strip()], "scope-based"


def required(reads):
    """Minimum classic scopes and fine-grained permissions for declared reads. Pure.

    Unrecognised names come back in `unknown` rather than being silently
    dropped, because a typo that quietly shrinks the requirement would make a
    token look over-scoped when it is not.
    """
    classic, fine, unknown = set(), set(), []
    for name in reads or []:
        key = str(name).strip().lower()
        if not key:
            continue
        if key not in NEEDS_CLASSIC:
            unknown.append(key)
            continue
        classic.update(NEEDS_CLASSIC[key])
        fine.update(NEEDS_FINE_GRAINED[key])
    return {"classic": sorted(classic), "fine_grained": sorted(fine),
            "unknown": sorted(unknown)}


def capabilities(scopes):
    """Every write verb the given scopes authorize, deduplicated. Pure."""
    verbs = []
    for scope in sorted(set(scopes or [])):
        for verb in CAPABILITIES.get(scope, ()):
            if verb not in verbs:
                verbs.append(verb)
    return verbs


def excess(held, needed_classic):
    """Scopes held that no declared read asks for. Pure.

    A plain difference against the minimum set, which is enough because the
    minimum is exact: anything outside it was not asked for, whether it is
    broader or merely unrelated.
    """
    needed = set(needed_classic or [])
    return sorted(s for s in set(held or []) if s not in needed)


def blast_radius(user, held):
    """How many repositories the write verbs reach. Pure.

    Counts from the GET /user body rather than a listing, so the audit stays one
    request. Returns None for the count when the body did not say, because a
    guessed number in a security report is worse than an absent one.
    """
    writes = [s for s in (held or []) if s in CAPABILITIES]
    body = user if isinstance(user, dict) else {}
    total = 0
    seen_any = False
    for field in ("public_repos", "total_private_repos"):
        value = body.get(field)
        if isinstance(value, int):
            total += value
            seen_any = True
    return {"repositories": total if seen_any else None,
            "write_scopes": writes,
            "verbs": capabilities(writes)}


def verdict(kind, held, needed, radius):
    """Turn the inventory into a finding about a system that is working. Pure."""
    if kind == "not-scope-based":
        return ("not-scope-based",
                "no x-oauth-scopes header, so this credential carries "
                "per-repository permissions rather than account-wide scopes. "
                "There is nothing to narrow here.")

    unnecessary = excess(held, needed["classic"])
    dangerous = [s for s in unnecessary if s in CAPABILITIES]
    reach = radius.get("repositories")
    where = ("%d repositories" % reach) if reach is not None \
        else "every repository the account can reach"

    if dangerous:
        return ("over-scoped",
                "%d scope(s) held that no declared read needs, and %d of them "
                "grant write across %s: %s"
                % (len(unnecessary), len(dangerous), where, ", ".join(dangerous)))
    if unnecessary:
        return ("unused-scopes",
                "%d scope(s) held that no declared read needs: %s. None of them "
                "grant write, so this is untidy rather than dangerous."
                % (len(unnecessary), ", ".join(unnecessary)))
    if set(held or []) & UNAVOIDABLY_BROAD:
        return ("coarse-by-construction",
                "the scopes held are the minimum a classic token can have for "
                "these reads, and they still grant write across %s. No classic "
                "token is narrower than this one; the repair is a different "
                "credential type." % where)
    return ("least-privilege",
            "every scope held is required by a declared read, and none of them "
            "grant write.")


def get(session, path):
    """One GET. Returns (status, json-or-None, headers)."""
    url = API + path if path.startswith("/") else path
    r = session.get(url, timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body, dict(r.headers)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--needs", default="",
                    help="comma-separated list of what the job reads: " +
                         ", ".join(sorted(NEEDS_CLASSIC)))
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN to the credential you want inventoried")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    status, body, headers = get(session, "/user")
    if status == 401:
        log.error("GET /user returned 401. This credential does not "
                  "authenticate, which is a different note")
        return 2
    if status != 200:
        log.error("GET /user returned %d; cannot inventory the credential", status)
        return 2

    held, kind = held_scopes(headers)
    needed = required([s for s in args.needs.split(",") if s.strip()])
    radius = blast_radius(body, held)

    log.info("authenticated as %s", (body or {}).get("login", "an unnamed user"))
    log.info("held:     %s", "header absent" if held is None
             else (", ".join(held) or "none"))
    log.info("required: %s", ", ".join(needed["classic"]) or
             "no scope at all for the declared reads")
    if needed["unknown"]:
        log.warning("unrecognised read(s) %s were ignored; a typo here makes a "
                    "token look broader than it is",
                    ", ".join(needed["unknown"]))

    for verb in radius["verbs"]:
        log.warning("this credential can %s", verb)

    state, detail = verdict(kind, held, needed, radius)
    log.info("%s: %s", state, detail)

    if state in ("over-scoped", "coarse-by-construction"):
        log.info("repair: mint a fine-grained token limited to the repositories "
                 "this job reads, with exactly: %s",
                 ", ".join(needed["fine_grained"]) or "Metadata: Read")
        log.info("repair: run both credentials side by side for one cycle, "
                 "compare the output, then revoke the classic token.")
    if state == "unused-scopes":
        log.info("repair: re-mint without %s. Scopes cannot be removed from an "
                 "existing classic token.",
                 ", ".join(excess(held, needed["classic"])))

    log.info("note: a read-only token can only inventory itself. It cannot "
             "enumerate the other tokens on this account or say who else holds "
             "a copy of this one.")

    print(json.dumps({"kind": kind, "held": held, "required": needed,
                      "blast_radius": radius, "state": state}, indent=2))
    return 0 if state in ("least-privilege", "not-scope-based") else 1


if __name__ == "__main__":
    sys.exit(main())
