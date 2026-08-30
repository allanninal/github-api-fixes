"""Tell apart the several different failures GitHub hides behind one 404.

Read only. GET requests and nothing else: a token with read access is enough.
The repair is printed, never performed, because this script holds a credential
that can reach private repositories.
"""
import argparse
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_404_triage")

API = "https://api.github.com"
UA = "github-404-triage/1.0"

# Longest prefixes first so a future prefix that extends an existing one cannot
# be swallowed by its shorter neighbour.
PREFIXES = (
    ("github_pat_", "fine-grained PAT"),
    ("ghp_", "classic PAT"),
    ("gho_", "OAuth user token"),
    ("ghu_", "App user-to-server token"),
    ("ghs_", "App installation token"),
    ("ghr_", "App refresh token"),
)


def token_kind(token):
    """Name the credential from its prefix. Pure, and it never leaves the machine.

    Which check is worth making depends entirely on what kind of token this is:
    scopes are meaningless for an App installation token, and the installation
    question is meaningless for a classic PAT. A prefix comparison answers that
    for free, before a single request is spent.
    """
    value = (token or "").strip()
    for prefix, name in PREFIXES:
        if value.startswith(prefix):
            return name
    return "unknown"


def scope_list(header_value):
    """Read x-oauth-scopes into a list, keeping absent and empty apart.

    A classic token with nothing ticked sends the header with an empty value.
    A fine-grained token or an App token does not send it at all. Collapsing both
    to an empty list loses the one signal that decides which repair to print, so
    absence is None and emptiness is [].
    """
    if header_value is None:
        return None
    return [s.strip() for s in header_value.split(",") if s.strip()]


def verdict(probe):
    """Classify one 404. Pure, so the rules are readable rather than inferred.

    `probe` carries what the reads found: repo_status, authenticated, scopes
    (None when the token does not use them), token_kind, and in_installation
    (None when the question does not apply). Returns (state, detail).
    """
    status = probe.get("repo_status")

    if not probe.get("authenticated"):
        return ("bad-credentials",
                "GET /user did not authenticate. Every private repository 404s "
                "for a dead token while every public one answers 200, which is "
                "why this looks like a per-repository permission problem.")

    if status == 200:
        return ("visible", "the repository answered 200")
    if status == 403:
        return ("plain-403",
                "403 rather than 404, which is the honest one: rate limit, org "
                "IP allow list, or a policy that blocks this app. Read the "
                "message body and x-ratelimit-remaining before assuming access.")
    if status != 404:
        return ("unexpected", "HTTP %s is not the masked case" % (status,))

    kind = probe.get("token_kind")
    if kind == "App installation token":
        inside = probe.get("in_installation")
        if inside is True:
            return ("metadata-permission",
                    "the repository is inside the installation, so it exists and "
                    "you reach it. Every repository endpoint requires "
                    "Metadata: Read; without it the repository itself 404s.")
        if inside is False:
            return ("not-in-installation",
                    "the installation does not include this repository. "
                    "repository_selection is 'selected' and this one was never "
                    "ticked, so it is outside the token's world entirely.")
        return ("installation-unknown",
                "GET /installation/repositories could not be read, so the "
                "installation question is open. Retry with the installation "
                "token the failing call actually uses.")

    scopes = probe.get("scopes")
    if scopes is None:
        return ("repository-not-granted",
                "no x-oauth-scopes header, so this is a fine-grained token. "
                "Those grant repositories one at a time: this one is not in the "
                "token's repository list, or Metadata: Read is not on it.")
    if "repo" not in scopes:
        return ("missing-scope",
                "the token carries %s and not 'repo'. Public repositories answer "
                "and private ones return exactly this 404."
                % (", ".join(scopes) or "no scopes at all",))

    return ("no-access-or-gone",
            "the token authenticates and carries 'repo', so the scope is not the "
            "problem. What is left is an account that was never granted access, "
            "or a repository that is genuinely gone. GitHub returns the same 404 "
            "for both on purpose and no header separates them.")


def get(session, url, **params):
    return session.get(url, params=params, timeout=30)


def installation_repos(session, api, limit=2000):
    """Every repository inside this installation, or None if it cannot be read.

    Paged rather than trusted from one page: total_count is the size of the
    installation, and the repositories array is one page of it.
    """
    out = []
    page = 1
    while len(out) < limit:
        r = get(session, api + "/installation/repositories", per_page=100, page=page)
        if r.status_code != 200:
            return None
        items = r.json().get("repositories", [])
        out.extend(items)
        if len(items) < 100:
            break
        page += 1
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo", help="owner/name of the repository that returns 404")
    ap.add_argument("--api", default=API,
                    help="API host, for GitHub Enterprise Server")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2
    if "/" not in args.repo:
        log.error("pass the repository as owner/name")
        return 2
    owner, name = args.repo.split("/", 1)

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub rejects requests with no User-Agent outright, which is its own
        # confusing 403 and not the one this script is about.
        "User-Agent": UA,
    })

    kind = token_kind(token)
    me = get(session, args.api + "/user")
    probe = {
        "token_kind": kind,
        "authenticated": me.status_code == 200,
        "scopes": scope_list(me.headers.get("x-oauth-scopes")),
        "in_installation": None,
    }
    login = me.json().get("login") if probe["authenticated"] else None

    repo = get(session, "%s/repos/%s/%s" % (args.api, owner, name))
    probe["repo_status"] = repo.status_code

    if kind == "App installation token" and repo.status_code == 404:
        repos = installation_repos(session, args.api)
        if repos is not None:
            full = args.repo.lower()
            probe["in_installation"] = any(
                str(r.get("full_name") or "").lower() == full for r in repos)

    state, detail = verdict(probe)
    line = "%-22s %s  %s" % (state, args.repo, detail)
    if state == "visible":
        log.info("%s (authenticated as %s)", line, login)
        return 0

    log.warning(line)
    log.warning("  token: %s, login: %s, scopes: %s", kind, login,
                "absent" if probe["scopes"] is None else (probe["scopes"] or "none"))
    repairs = {
        "bad-credentials": "re-mint the token and assert GET /user returns the "
                           "expected login at startup",
        "missing-scope": "re-create the classic token with the 'repo' scope, or "
                         "move to a fine-grained token listing this repository",
        "repository-not-granted": "add this repository to the fine-grained "
                                  "token's repository access, with Metadata: Read",
        "not-in-installation": "add the repository to the App installation, or "
                               "switch the installation to All repositories",
        "metadata-permission": "add Metadata: Read to the App and have each "
                               "installation accept the updated permissions",
        "no-access-or-gone": "grant %s access to the repository, or confirm with "
                             "somebody who can see it that it still exists" % (login,),
    }
    if state in repairs:
        log.warning("  repair: %s", repairs[state])
    return 1


if __name__ == "__main__":
    sys.exit(main())
