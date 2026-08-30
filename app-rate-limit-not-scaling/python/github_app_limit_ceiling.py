"""Say whether a GitHub App installation has the rate-limit ceiling it earns.

Read only. Two GETs: the rate-limit endpoint, which does not consume quota, and
a one-item page of the installation's repositories, which is the cheapest way to
learn how big the installation is. Nothing is minted, widened or changed.

An installation's hourly ceiling starts at 5,000 and grows with the number of
repositories and users the installation covers, to a maximum of 12,500; an
Enterprise Cloud organization gets a flat 15,000 instead. An installation
restricted to a handful of selected repositories never earns the growth, so a
large organization behind a narrow installation throttles at the same ceiling a
single user gets.

This is not the note about draining an hourly window. It is about the size of
the window before anything draws from it.

Environment:

    GITHUB_INSTALLATION_TOKEN   an installation access token
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_app_limit_ceiling")

API = "https://api.github.com"
UA = "github-app-limit-ceiling/1.0"

# The documented shape of the ceiling. Kept as named constants because every one
# of them turns up in the output, and a reader comparing the script against the
# documentation should be able to find them in one place.
BASELINE = 5000
PER_UNIT = 50
SCALING_FLOOR = 20
FREE_CEILING = 12500
ENTERPRISE_CEILING = 15000
ANONYMOUS = 60


def entitled(repositories, users=None, enterprise=False):
    """The hourly ceiling an installation of this size earns. Pure.

    users is allowed to be None, because a read-only installation token usually
    cannot count the members of an organization. The user term only ever adds,
    so treating an unknown count as zero makes the result a lower bound rather
    than a guess: a measured limit below this number is a real shortfall
    whatever the true membership is.
    """
    if enterprise:
        return ENTERPRISE_CEILING
    try:
        repos = int(repositories or 0)
    except (TypeError, ValueError):
        repos = 0
    try:
        people = int(users or 0)
    except (TypeError, ValueError):
        people = 0
    extra = max(0, repos - SCALING_FLOOR) + max(0, people - SCALING_FLOOR)
    return min(FREE_CEILING, BASELINE + PER_UNIT * extra)


def is_lower_bound(users):
    """Whether the entitlement was computed without the user term. Pure."""
    return users is None


def classify_ceiling(limit):
    """Name the ceiling a credential was actually given. Pure.

    The names matter more than the numbers downstream, because the repair for a
    ceiling that sits at the floor and the repair for one already at the cap are
    opposite pieces of advice.
    """
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return "unknown"
    if value == ANONYMOUS:
        return "unauthenticated"
    if value == ENTERPRISE_CEILING:
        return "enterprise"
    if value == FREE_CEILING:
        return "at-cap"
    if value == BASELINE:
        return "baseline"
    if BASELINE < value < FREE_CEILING:
        return "scaled"
    return "unknown"


def selection_of(view):
    """The repository_selection on an installation view, normalised. Pure."""
    if not isinstance(view, dict):
        return "unknown"
    raw = str(view.get("repository_selection") or "").strip().lower()
    return raw if raw in ("all", "selected") else "unknown"


def reachable(view):
    """How many repositories the installation covers, or None. Pure."""
    if not isinstance(view, dict):
        return None
    raw = view.get("total_count")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def shortfall(limit, entitlement):
    """Requests an hour the installation is not getting, or 0. Pure."""
    try:
        have, earned = int(limit), int(entitlement)
    except (TypeError, ValueError):
        return 0
    return max(0, earned - have)


def sustainable_repos(limit, calls_per_repo):
    """How many repositories a loop of this cost fits under the ceiling. Pure."""
    try:
        ceiling, cost = int(limit), int(calls_per_repo)
    except (TypeError, ValueError):
        return None
    if cost <= 0:
        return None
    return ceiling // cost


def verdict(limit, selection, covered, account_repos=None, users=None,
            enterprise=False, installation_seen=True):
    """Turn the two reads into a finding. Pure.

    covered is the size of the installation. account_repos, where the caller
    could learn it, is the size of the account behind the installation: the
    difference between those two is what makes a narrow installation narrow.
    """
    klass = classify_ceiling(limit)
    if klass == "unauthenticated":
        return ("unauthenticated",
                "the ceiling is 60/hour, which is the anonymous ceiling. This "
                "credential is not reaching GitHub as an installation at all.")
    if not installation_seen:
        return ("not-an-installation",
                "the ceiling is %s/hour and the installation endpoint did not "
                "answer, so this is a user or Actions credential rather than an "
                "installation token. Installation scaling does not apply to it."
                % limit)
    if klass == "enterprise":
        return ("enterprise",
                "the ceiling is 15000/hour, the flat Enterprise Cloud ceiling. "
                "Widening the installation cannot raise it further.")
    earned = entitled(account_repos if account_repos is not None else covered,
                      users, enterprise)
    if klass == "at-cap":
        return ("at-cap",
                "the ceiling is 12500/hour, the maximum outside Enterprise "
                "Cloud. There is no more to earn: spend fewer requests.")
    gap = shortfall(limit, earned)
    if gap and selection == "selected":
        return ("narrow-installation",
                "the ceiling is %s/hour, and an installation covering %s "
                "repositories would be entitled to at least %d/hour. The "
                "selection is what is capping it, not the account."
                % (limit, account_repos if account_repos is not None else covered,
                   earned))
    if gap:
        return ("below-entitlement",
                "the ceiling is %s/hour against an entitlement of at least "
                "%d/hour for this size. The installation is narrower than the "
                "size used for the comparison." % (limit, earned))
    if klass == "baseline":
        return ("baseline",
                "the ceiling is 5000/hour and the installation covers %s "
                "repositories, which is too few to earn any scaling. This "
                "ceiling is real: the repair is on the usage side."
                % covered)
    return ("scaled",
            "the ceiling is %s/hour, which matches an installation this size."
            % limit)


def repair(state, covered=None, account_repos=None):
    """The sentence a reader has to act on. Pure."""
    if state == "narrow-installation":
        return ("widen the installation to all repositories if the App "
                "legitimately needs org-wide reach, which raises the ceiling as "
                "a side effect. If it does not, keep the narrow selection and "
                "cut request volume instead: conditional requests, a bigger "
                "per_page, one GraphQL query for a fan-out of REST calls.")
    if state in ("at-cap", "enterprise", "baseline"):
        return ("nothing on the installation. This ceiling is the one you get, "
                "so the only lever left is spending fewer requests per unit of "
                "work.")
    if state == "unauthenticated":
        return ("send the installation access token in the Authorization "
                "header. Nothing about scaling matters while the requests are "
                "arriving anonymously.")
    if state == "not-an-installation":
        return ("point the check at an installation access token. A user token "
                "gets a flat 5000 and never scales, so comparing it against an "
                "installation entitlement is meaningless.")
    if state == "below-entitlement":
        return ("check repository_selection and the account behind the "
                "installation before widening anything: the numbers disagree "
                "for a reason this script could not see.")
    return "nothing."


def get(session, path):
    """One GET. Returns (status, json-or-None)."""
    url = API + path if path.startswith("/") else path
    r = session.get(url, timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body


def core_limits(session):
    """The core and graphql ceilings from the free rate-limit endpoint."""
    status, body = get(session, "/rate_limit")
    if status != 200 or not isinstance(body, dict):
        log.error("GET /rate_limit returned %d", status)
        return None, None
    resources = body.get("resources") or {}
    core = (resources.get("core") or {}).get("limit")
    graphql = (resources.get("graphql") or {}).get("limit")
    return core, graphql


def installation_view(session):
    """total_count and repository_selection, without fetching repositories."""
    status, body = get(session, "/installation/repositories?per_page=1")
    if status != 200 or not isinstance(body, dict):
        return None
    return body


def account_size(session, org):
    """Repositories on the account behind the installation, where readable."""
    if not org:
        return None
    status, body = get(session, "/orgs/%s" % org)
    if status != 200 or not isinstance(body, dict):
        log.info("GET /orgs/%s returned %d; the account size is not readable "
                 "from here, so the comparison uses the installation size",
                 org, status)
        return None
    public = body.get("public_repos") or 0
    private = body.get("total_private_repos") or 0
    try:
        return int(public) + int(private)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--org", default=os.environ.get("GITHUB_ORG"),
                    help="the account behind the installation, to size it")
    ap.add_argument("--users", type=int, default=None,
                    help="members of that account, if you know it; the API "
                         "rarely tells a read-only installation token")
    ap.add_argument("--calls-per-repo", type=int, default=10,
                    help="calls your loop makes per repository per hour")
    ap.add_argument("--enterprise", action="store_true",
                    help="the account is on GitHub Enterprise Cloud")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_INSTALLATION_TOKEN")
    if not token:
        log.error("set GITHUB_INSTALLATION_TOKEN to an installation access "
                  "token. A user token has a flat ceiling and nothing here "
                  "applies to it")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    core, graphql = core_limits(session)
    if core is None:
        return 2
    log.info("core ceiling: %s/hour, graphql %s/hour", core, graphql)

    view = installation_view(session)
    covered = reachable(view)
    selection = selection_of(view)
    if view is None:
        log.info("the installation endpoint did not answer for this token")
    else:
        log.info("installation: repository_selection=%s, %s repository/"
                 "repositories reachable", selection, covered)

    org_repos = account_size(session, args.org) if selection == "selected" else None
    state, detail = verdict(core, selection, covered, org_repos, args.users,
                            args.enterprise, installation_seen=view is not None)
    log.info("%s: %s", state, detail)
    log.info("repair: %s", repair(state, covered, org_repos))

    fits = sustainable_repos(core, args.calls_per_repo)
    if fits is not None:
        log.info("budget: %s/hour serves %d repositories at %d call(s) each",
                 core, fits, args.calls_per_repo)

    print(json.dumps({
        "core_limit": core,
        "graphql_limit": graphql,
        "repository_selection": selection,
        "repositories_covered": covered,
        "account_repositories": org_repos,
        "entitlement_is_lower_bound": is_lower_bound(args.users),
        "entitled": entitled(org_repos if org_repos is not None else covered,
                             args.users, args.enterprise),
        "ceiling_class": classify_ceiling(core),
        "state": state,
        "detail": detail,
        "repositories_supported": fits,
    }, indent=2, default=str))
    return 1 if state in ("narrow-installation", "below-entitlement",
                          "unauthenticated") else 0


if __name__ == "__main__":
    sys.exit(main())
