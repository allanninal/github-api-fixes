"""Say whether an installation access token was narrowed below what a job needs.

Read only. One paginated GET for what the token reaches, one GET per
repository the job names. The token endpoint that would mint a wider token is
a write and is not called here: the script reads the token you already hold
and prints the mint request you should be making instead.

An installation access token can be minted for fewer repositories and fewer
permissions than the installation holds, by naming them in the mint request
body. The result is a 404 on a repository the App is plainly installed on,
from one code path, while every other path using the same App works.

There is a blind spot and the report states it. A token cannot report its own
permission map; the mint response echoed it back to your own code and no read
recovers it afterwards. Pass that saved response with --grant to make the
permission half exact, or the script reports it as unseen rather than passing.

Environment:

    GITHUB_INSTALLATION_TOKEN   the token the failing job holds
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_token_reach")

API = "https://api.github.com"
UA = "github-token-reach/1.0"

RANK = {"none": 0, "read": 1, "write": 2, "admin": 3}


def rank(level):
    """A permission level as a comparable integer. Pure."""
    return RANK.get(str(level or "none").strip().lower(), 0)


def parse_needs(spec):
    """Turn contents:read,issues:write into a map. Pure.

    A bare name with no colon is taken as read, which is the level somebody
    means when they write down a permission without thinking about it.
    """
    out = {}
    for chunk in str(spec or "").split(","):
        item = chunk.strip()
        if not item:
            continue
        name, _, level = item.partition(":")
        name = name.strip().lower()
        if name:
            out[name] = (level.strip().lower() or "read")
    return out


def parse_grant(body):
    """Read a saved mint response into the facts it carries. Pure.

    Only three fields matter and any of them can be absent. permissions is
    None rather than an empty map when it is missing, because "this token was
    granted nothing" and "we did not see the grant" are opposite findings.
    """
    body = body if isinstance(body, dict) else {}
    permissions = body.get("permissions")
    repos = body.get("repositories")
    names = []
    if isinstance(repos, list):
        for repo in repos:
            if isinstance(repo, dict) and repo.get("full_name"):
                names.append(str(repo["full_name"]))
            elif isinstance(repo, str):
                names.append(repo)
    return {"permissions": permissions if isinstance(permissions, dict) else None,
            "repository_selection": body.get("repository_selection"),
            "repositories": names}


def repo_gap(reachable, needed):
    """Needed repositories the token cannot reach. Pure.

    Case-insensitive, because GitHub treats owner/name that way and the
    configuration file that lists them does not.
    """
    have = {str(r).strip().lower() for r in (reachable or [])}
    return [r for r in (needed or []) if str(r).strip().lower() not in have]


def permission_shortfall(granted, needed):
    """Needed permissions the token holds at a lower level. Pure.

    Returns None when the grant was never seen, which the caller reports as a
    blind spot rather than as a pass.
    """
    if granted is None:
        return None
    out = []
    for name, wanted in sorted((needed or {}).items()):
        have = (granted or {}).get(name)
        if rank(have) < rank(wanted):
            out.append((name, str(wanted), str(have) if have else "absent"))
    return out


def verdict(alive, missing_repos, shortfall, selection):
    """Turn reach, grant and need into a finding. Pure.

    Order matters: an unreachable repository is reported before a permission
    shortfall, because a 404 is the symptom people arrive with and fixing the
    permission first would leave them with the same 404.
    """
    if not alive:
        return ("token-not-alive",
                "GET /installation/repositories did not return 200, so this "
                "is not a working installation access token and the "
                "narrowing question does not arise yet.")
    if missing_repos:
        return ("repos-out-of-reach",
                "%s not in this token's repository set, so every call about "
                "them answers 404 whatever the App holds. Widen the "
                "repository list in the mint request."
                % ", ".join(missing_repos))
    if shortfall is None:
        return ("narrowing-not-visible",
                "every repository the job needs is reachable. The permission "
                "half cannot be checked: a token does not report its own "
                "permission map, and no saved mint response was supplied.")
    if shortfall:
        return ("permissions-below-need",
                "%s. The mint request asked for less than the job uses, which "
                "fails as 403 rather than as 404."
                % "; ".join("%s is %s, the job needs %s" % (n, h, w)
                            for n, w, h in shortfall))
    if str(selection or "").strip().lower() == "selected":
        return ("narrowed-but-sufficient",
                "this token is narrowed to a repository subset and the subset "
                "still covers the job. Nothing to change.")
    return ("reach-covers-the-job",
            "this token reaches every repository the job needs and holds "
            "every permission at the level it asked for.")


def repair(state, missing_repos, shortfall):
    """The change to make, in the mint request rather than in the App. Pure."""
    if state == "repos-out-of-reach":
        return ("add %s to the repository list in the token request this job "
                "makes. If the installation already covers them, the App does "
                "not change at all." % ", ".join(missing_repos))
    if state == "permissions-below-need":
        return ("raise %s in the permission map of the token request. If the "
                "installation does not hold it either, that is an App "
                "permission problem instead."
                % ", ".join("%s to %s" % (n, w) for n, w, _ in shortfall))
    if state == "narrowing-not-visible":
        return ("keep the mint response your code already receives, with the "
                "token value stripped, and pass it back in. It is the only "
                "place the granted permission map is ever visible.")
    return "nothing. This token is not the constraint."


def get(session, path):
    """One GET. Returns (status, json-or-None)."""
    url = API + path if path.startswith("/") else path
    r = session.get(url, timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body


def reachable_repositories(session, pages=10):
    """Every repository this token reaches. Returns (alive, names, selection)."""
    names, selection, alive = [], None, False
    for page in range(1, pages + 1):
        status, body = get(session,
                           "/installation/repositories?per_page=100&page=%d" % page)
        if status != 200 or not isinstance(body, dict):
            if page == 1:
                log.error("GET /installation/repositories returned %d; only an "
                          "installation access token can answer it", status)
            break
        alive = True
        selection = body.get("repository_selection", selection)
        rows = body.get("repositories") or []
        names.extend(str(r.get("full_name")) for r in rows
                     if isinstance(r, dict) and r.get("full_name"))
        if len(rows) < 100:
            break
    return alive, names, selection


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", action="append", default=[],
                    help="a repository the job needs, as owner/name; repeatable")
    ap.add_argument("--needs", default="",
                    help="permissions the job uses, as contents:read,issues:write")
    ap.add_argument("--grant", default=None,
                    help="path to the saved mint response body, token stripped")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_INSTALLATION_TOKEN") or \
        os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_INSTALLATION_TOKEN to the token the failing job "
                  "holds. The narrowing is a property of that token and of no "
                  "other credential")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    alive, reach, selection = reachable_repositories(session)
    if alive:
        log.info("token reaches %d repository(ies), repository_selection=%s",
                 len(reach), selection or "unreported")

    # Confirming per repository rather than trusting the list, which can be
    # truncated by a pagination cap the caller did not notice.
    confirmed = []
    for name in args.repo:
        if not alive:
            break
        status, _ = get(session, "/repos/%s" % name.strip())
        log.info("GET /repos/%s returned %d", name.strip(), status)
        if status == 200:
            confirmed.append(name.strip())
    reach_all = sorted({*reach, *confirmed})

    grant = {"permissions": None, "repository_selection": selection,
             "repositories": []}
    if args.grant:
        try:
            with open(args.grant, encoding="utf-8") as fh:
                grant = parse_grant(json.load(fh))
        except (OSError, ValueError) as exc:
            log.error("could not read the saved mint response: %s", exc)
        else:
            selection = grant.get("repository_selection") or selection

    missing = repo_gap(reach_all, args.repo)
    shortfall = permission_shortfall(grant["permissions"], parse_needs(args.needs))
    state, detail = verdict(alive, missing, shortfall, selection)
    log.info("%s: %s", state, detail)
    log.info("repair: %s", repair(state, missing, shortfall))

    print(json.dumps({"reachable": reach_all, "repository_selection": selection,
                      "needed_repositories": args.repo,
                      "missing_repositories": missing,
                      "permission_shortfall": shortfall,
                      "state": state}, indent=2, default=str))
    return 1 if state in ("repos-out-of-reach", "permissions-below-need") else 0


if __name__ == "__main__":
    sys.exit(main())
