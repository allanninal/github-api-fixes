"""Report how much of an organization a GitHub App installation can actually see.

Read only. Two GET requests and no writes: an installation token plus a token
that can read the organization is enough. The repair is printed, never performed.
"""
import argparse
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_app_coverage_audit")

API = "https://api.github.com"
UA = "github-app-coverage-audit/1.0"


def expected_total(org):
    """Repositories the organization actually has, or None if it cannot be known.

    public_repos plus total_private_repos. total_private_repos is only returned
    to callers with enough access; when it is absent the public count is a floor
    and not a total. Returning it anyway would produce a coverage figure that
    understates the gap, so this returns None and lets the caller say so.
    """
    if not isinstance(org, dict):
        return None
    public = org.get("public_repos")
    private = org.get("total_private_repos")
    if public is None or private is None:
        return None
    return int(public) + int(private)


def coverage(selection, seen, expected):
    """Compare what the installation sees against what exists. Pure.

    Returns (state, detail). A `selected` installation whose count happens to
    match today is deliberately not the same state as `all`: it is correct now
    and nothing keeps it correct.
    """
    sel = str(selection or "").strip().lower()

    if sel == "all":
        return ("all-repositories",
                "%d repository(ies) visible, and repository_selection is 'all', "
                "so repositories created later join the installation "
                "automatically." % (seen,))

    if sel != "selected":
        return ("unknown-selection",
                "repository_selection is %r, which is neither 'all' nor "
                "'selected'. Do not assume coverage from a value you cannot "
                "interpret." % (selection,))

    if expected is None:
        return ("unmeasured",
                "%d repository(ies) selected. The organization's own total is "
                "not readable with this credential, so this is a count and not a "
                "coverage figure. Say so in the report rather than implying "
                "completeness." % (seen,))

    if seen > expected:
        return ("inconsistent",
                "%d repository(ies) visible against an organization total of %d. "
                "The installation spans more than this organization, or one of "
                "the two counts is stale. Resolve it before quoting either."
                % (seen, expected))

    if seen == expected:
        return ("selected-complete",
                "%d of %d today, and nothing keeps it that way: a 'selected' "
                "installation does not pick up repositories created later, so "
                "this is complete by coincidence." % (seen, expected))

    return ("partial",
            "%d of %d repositories. Every endpoint answers truthfully about "
            "those %d and says nothing at all about the other %d, so a clean "
            "report here covers %.0f%% of the organization."
            % (seen, expected, seen, expected - seen, 100.0 * seen / expected))


def get(session, url, **params):
    return session.get(url, params=params, timeout=30)


def installation_view(session, api):
    """repository_selection, total_count and the full names, from inside the App."""
    names = []
    selection, total = None, 0
    page = 1
    while True:
        r = get(session, api + "/installation/repositories", per_page=100, page=page)
        if r.status_code != 200:
            raise SystemExit("%d from GET /installation/repositories: this needs "
                             "an App installation token" % (r.status_code,))
        body = r.json()
        if page == 1:
            selection = body.get("repository_selection")
            total = int(body.get("total_count") or 0)
        items = body.get("repositories", [])
        names.extend(str(r_.get("full_name") or "") for r_ in items)
        if len(items) < 100:
            break
        page += 1
    return selection, total, names


def org_repo_names(session, api, org):
    """Every repository in the organization, from outside the installation."""
    names = []
    page = 1
    while True:
        r = get(session, "%s/orgs/%s/repos" % (api, org), per_page=100, page=page)
        if r.status_code != 200:
            return None
        items = r.json()
        names.extend(str(x.get("full_name") or "") for x in items)
        if len(items) < 100:
            break
        page += 1
    return names


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--org", required=True,
                    help="the organization the installation is meant to cover")
    ap.add_argument("--api", default=API,
                    help="API host, for GitHub Enterprise Server")
    ap.add_argument("--list-missing", action="store_true",
                    help="name the repositories outside the installation")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (an App installation token, read-only)")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    selection, seen, inside = installation_view(session, args.api)

    org_response = get(session, "%s/orgs/%s" % (args.api, args.org))
    expected = expected_total(org_response.json()) if org_response.status_code == 200 else None

    state, detail = coverage(selection, seen, expected)
    line = "%-18s %s" % (state, detail)
    if state == "all-repositories":
        log.info(line)
        return 0

    log.warning(line)
    if args.list_missing:
        outside = org_repo_names(session, args.api, args.org)
        if outside is None:
            log.warning("  the organization's repository list is not readable "
                        "with this credential, so the missing names cannot be "
                        "printed. The counts above still stand.")
        else:
            have = {n.lower() for n in inside}
            missing = sorted(n for n in outside if n.lower() not in have)
            for name in missing[:50]:
                log.warning("  outside the installation: %s", name)
            if len(missing) > 50:
                log.warning("  ... and %d more", len(missing) - 50)

    log.warning("  repair: switch the installation to All repositories, or add "
                "the missing repositories to it. Then have the tool print its "
                "own coverage next to its findings, so a clean report can never "
                "again appear without the number of repositories behind it.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
