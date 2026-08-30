"""Say whether a GitHub App is installed on one specific repository.

Read only. Three GETs: one unauthenticated existence check, and two presence
questions asked with the App's JWT, at repository scope and at account scope.
Nothing is installed, added, changed or minted.

An installation access token cannot see outside its installation, and GitHub
answers 404 rather than 403 for anything outside it, so a public repository the
App was never installed on is indistinguishable from one that does not exist.
GET /repos/{owner}/{repo}/installation is the call that answers directly.

The JWT is read from the environment and never printed. The output is three
status codes, two timestamps and a verdict.
"""
import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_app_installation_presence")

API = "https://api.github.com"
UA = "github-app-installation-presence/1.0"

# Owner and repository names GitHub will actually issue.
NAME = re.compile(r"[A-Za-z0-9._-]+")

# Every way a repository reference tends to arrive in a bug report.
PREFIXES = ("https://github.com/", "http://github.com/",
            "https://api.github.com/repos/", "git@github.com:")


def split_repo(value):
    """Reduce any repository reference to (owner, name). Pure.

    A browser URL, an API URL, a clone path and a plain owner/name pair all
    describe the same repository, and a bug report will contain whichever one
    the reporter had on their clipboard. Returns None when it is not a
    repository reference at all.
    """
    text = str(value or "").strip().rstrip("/")
    for prefix in PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    if text.endswith(".git"):
        text = text[:-4]
    parts = [p for p in text.split("/") if p]
    if len(parts) < 2:
        return None
    owner, name = parts[0], parts[1]
    if not NAME.fullmatch(owner) or not NAME.fullmatch(name):
        return None
    return (owner, name)


def account_route(owner, owner_type):
    """The account-scope installation route for this kind of owner. Pure.

    Organizations and user accounts have separate routes and the wrong one
    404s for reasons that have nothing to do with the App, which would be a
    very annoying way to get a false finding.
    """
    if str(owner_type or "").lower() == "user":
        return "/users/%s/installation" % owner
    return "/orgs/%s/installation" % owner


def parse_iso(value):
    """Parse an ISO-8601 timestamp into epoch seconds. Pure. None if unusable."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def visibility(status):
    """What the unauthenticated read proved about the repository. Pure."""
    if status == 200:
        return ("public-repo",
                "the repository exists and is publicly readable, so whatever "
                "is wrong is on your side of the request.")
    if status == 404:
        return ("not-public-or-absent",
                "an unauthenticated read also returns 404, which means the "
                "repository is private or does not exist. A read-only check "
                "cannot separate those two, and neither can anyone else "
                "without access.")
    return ("visibility-unknown",
            "the unauthenticated read returned something other than 200 or "
            "404, so nothing was established about the repository itself.")


def classify(repo_status, account_status):
    """Turn two presence questions into one verdict. Pure.

    Repository scope is asked first because a 200 there ends the matter. The
    account-scope answer only matters when the repository-scope answer is no,
    and it is what splits one 404 into two different repairs.
    """
    if repo_status in (401, 403) or account_status in (401, 403):
        return ("jwt-not-accepted",
                "the App JWT was refused, so nothing was learned about any "
                "installation. Fix the JWT first; a signing or clock fault "
                "looks like an absent installation from here.")
    if repo_status == 200:
        return ("installed-on-this-repo",
                "the App is installed on this repository, so a 404 from your "
                "integration is about something else: a permission, a wrong "
                "path, or a credential that is not this App's.")
    if repo_status == 404 and account_status == 200:
        return ("installed-on-account-not-repo",
                "the App is installed on the account and this repository is "
                "not in the installation. The installation is set to selected "
                "repositories and this one was never selected.")
    if repo_status == 404 and account_status == 404:
        return ("not-installed-on-account",
                "the App is not installed anywhere on this account. Somebody "
                "with admin rights on the account has to install it; no "
                "permission or token change will do anything until they do.")
    return ("inconclusive",
            "the two presence checks did not return a pair this check "
            "recognises, so no verdict is safe.")


def creation_order(repo_created, installation_created, selection):
    """Say whether the repository is simply newer than the installation. Pure.

    This is the difference between a mistake and a recurring condition. A
    selection that does not grow will keep producing this finding for every
    repository created after it, and adding this one by hand fixes only today.
    """
    if str(selection or "").lower() == "all":
        return ("selection-covers-everything",
                "repository_selection is all, so new repositories are covered "
                "automatically and creation order is irrelevant.")
    if not selection:
        return ("selection-unknown",
                "no repository_selection was returned, so nothing can be said "
                "about how the installation grows.")
    if repo_created is None or installation_created is None:
        return ("creation-order-unknown",
                "one of the two creation dates is missing, so the order "
                "cannot be established.")
    if repo_created > installation_created:
        days = int((repo_created - installation_created) // 86400)
        return ("repo-created-after-installation",
                "this repository was created %d day(s) after the installation, "
                "and a selected-repositories installation does not gain new "
                "ones. Every repository created from now on will land in the "
                "same state." % days)
    return ("repo-predates-installation",
            "the repository already existed when the installation was "
            "configured, so it was left out deliberately or by oversight "
            "rather than by the passage of time.")


def repair_for(state, selection):
    """The sentence worth printing under a verdict. Pure."""
    if state == "installed-on-account-not-repo":
        if str(selection or "").lower() == "selected":
            return ("add this repository to the installation, or switch the "
                    "installation to all repositories so future ones are "
                    "covered without anybody remembering to.")
        return ("open the installation's configuration and add this "
                "repository to it.")
    if state == "not-installed-on-account":
        return ("install the App on this account. This needs somebody with "
                "admin rights on the account, and it is not something a token "
                "change can substitute for.")
    if state == "installed-on-this-repo":
        return ("nothing to repair here. If calls still fail, read the status "
                "code and the message rather than assuming coverage.")
    if state == "jwt-not-accepted":
        return ("fix the App JWT before reading anything above; an unusable "
                "JWT and an absent installation look the same from here.")
    return "no repair applies to this state."


def get(path, token=None):
    """One GET. Returns (status, body). The only network in this script."""
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": UA}
    if token:
        headers["Authorization"] = "Bearer " + token
    r = requests.get(API + path, timeout=30, headers=headers)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True,
                    help="the repository, as owner/name or as any GitHub URL "
                         "for it")
    args = ap.parse_args()

    target = split_repo(args.repo)
    if target is None:
        log.error("could not read %r as a repository reference", args.repo)
        return 2
    owner, name = target

    jwt = os.environ.get("GITHUB_APP_JWT")
    if not jwt:
        log.error("set GITHUB_APP_JWT to the App JWT. The per-repository "
                  "installation route is answered with the JWT, not with an "
                  "installation access token")
        return 2

    # No credential on this one on purpose: whether the repository is publicly
    # readable is a fact about the world, not about the App.
    public_status, public_body = get("/repos/%s/%s" % (owner, name))
    vis_state, vis_detail = visibility(public_status)
    log.info("%s: %s", vis_state, vis_detail)
    owner_type = None
    repo_created = None
    if isinstance(public_body, dict):
        owner_type = (public_body.get("owner") or {}).get("type")
        repo_created = parse_iso(public_body.get("created_at"))

    repo_status, repo_body = get("/repos/%s/%s/installation" % (owner, name), jwt)
    log.info("GET /repos/%s/%s/installation returned %d", owner, name, repo_status)

    route = account_route(owner, owner_type)
    account_status, account_body = get(route, jwt)
    log.info("GET %s returned %d", route, account_status)

    state, detail = classify(repo_status, account_status)
    log.info("%s: %s", state, detail)

    installation = repo_body if repo_status == 200 else account_body
    selection = None
    installation_created = None
    installation_id = None
    if isinstance(installation, dict):
        selection = installation.get("repository_selection")
        installation_created = parse_iso(installation.get("created_at"))
        installation_id = installation.get("id")
    if installation_id is not None:
        log.info("installation %s, repository_selection=%s",
                 installation_id, selection or "unknown")

    order_state, order_detail = creation_order(repo_created,
                                               installation_created, selection)
    log.info("%s: %s", order_state, order_detail)
    log.info("repair: %s", repair_for(state, selection))

    print(json.dumps({"owner": owner, "repo": name,
                      "public_status": public_status,
                      "repo_installation_status": repo_status,
                      "account_installation_status": account_status,
                      "account_route": route,
                      "repository_selection": selection,
                      "installation_id": installation_id,
                      "visibility": vis_state, "order": order_state,
                      "state": state}, indent=2))
    return 0 if state == "installed-on-this-repo" else 1


if __name__ == "__main__":
    sys.exit(main())
