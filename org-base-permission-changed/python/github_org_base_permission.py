"""Say whether an organization's base permission is why the repository list shrank.

Read only. Three GETs. Granting an account access to a repository is a write
and a decision somebody with admin has to make, so this script measures the
loss and prints the narrow repair.

The point of the note: default_repository_permission is the role every member
holds on repositories they were never explicitly added to. Moving it from read
to none is ordinary hardening and it removes implicit access everywhere at
once, so a read-only integration keeps succeeding and covers a tenth of what
it did yesterday.

What this can and cannot see: the field is readable with organization access,
and the account's reachable repositories are countable in one request. What is
not visible is which repositories were reachable yesterday. This measures the
gap now and compares it against the base permission you say you configured
for; it cannot replay history.

Environment:

    GITHUB_TOKEN    a read-only token for the account whose coverage shrank
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_org_base_permission")

API = "https://api.github.com"
UA = "github-org-base-permission/1.0"

# Weakest first. The documented values of default_repository_permission.
BASE_PERMISSIONS = ("none", "read", "write", "admin")

# What a member with no explicit grants gets, per base permission.
IMPLIES = {
    "none": "members get no role on repositories they were not added to "
            "individually or through a team. Every private repository in the "
            "organization is invisible to a member with no explicit grants.",
    "read": "every member can read every repository in the organization "
            "without being added to it.",
    "write": "every member can push to every repository in the organization "
             "without being added to it.",
    "admin": "every member administers every repository in the organization. "
             "This is rare and worth questioning on its own.",
}


def read_cost():
    """Requests this run will spend against the core quota. Pure."""
    return 3


def base_rank(value):
    """Position in the hierarchy, or -1 for something unrecognised. Pure."""
    try:
        return BASE_PERMISSIONS.index(str(value or "").strip().lower())
    except ValueError:
        return -1


def base_state(org_payload):
    """The organization's base permission. Pure. (value, detail).

    An absent field is "unreadable" rather than a default. The field is
    returned to callers with organization access, and a token without it would
    otherwise be reported as an organization that grants nothing.
    """
    if not isinstance(org_payload, dict):
        return (None, "no organization payload was read.")
    if "default_repository_permission" not in org_payload:
        return (None, "default_repository_permission was not returned. Reading "
                      "it needs organization access, so this is unreadable "
                      "rather than absent.")
    value = str(org_payload.get("default_repository_permission") or "").strip().lower()
    if base_rank(value) < 0:
        return (value or None, "the value %r is not one of the four documented "
                               "base permissions." % value)
    return (value, IMPLIES[value])


def link_parts(link_header):
    """Split a Link header into its entries. Pure. No regular expression.

    Split on the commas that separate entries and not on the ones inside a
    URL, because a URL in a Link header can carry commas of its own -- a
    search query, a list of fields -- and splitting the whole header throws
    away the half that holds the page number.
    """
    parts, current, depth = [], "", 0
    for ch in str(link_header or ""):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current)
    return parts


def last_page_from_link(link_header):
    """The page number of rel="last", or None. Pure. No regular expression."""
    if not link_header:
        return None
    for part in link_parts(link_header):
        if 'rel="last"' not in part and "rel=last" not in part:
            continue
        start = part.find("<")
        end = part.find(">", start + 1)
        if start < 0 or end < 0:
            continue
        url = part[start + 1:end]
        query = url.partition("?")[2]
        for field in query.split("&"):
            name, _, value = field.partition("=")
            if name == "page" and value.isdigit():
                return int(value)
    return None


def count_from_link(link_header, returned):
    """How many items the collection holds, at per_page=1. Pure. (count, how).

    With one item per page the last page number is the count. When everything
    fits on one page there is no rel="last" at all, and that case is reported
    as what it is rather than as a count of zero.
    """
    last = last_page_from_link(link_header)
    if last is not None:
        return (last, 'from rel="last" with per_page=1')
    if not returned:
        return (0, "the first page came back empty and carried no rel=\"last\"")
    return (int(returned), 'a single page with no rel="last", so this is what '
                           'came back rather than a measured count')


def org_total(org_payload):
    """How many repositories the organization holds. Pure. (count, detail)."""
    if not isinstance(org_payload, dict):
        return (None, "no organization payload was read.")
    public = org_payload.get("public_repos")
    private = org_payload.get("total_private_repos")
    if public is None and private is None:
        return (None, "neither repository count was returned, which needs "
                      "organization access.")
    total = int(public or 0) + int(private or 0)
    return (total, "public %s + private %s"
            % ("unreadable" if public is None else public,
               "unreadable" if private is None else private))


def coverage_state(visible, total):
    """Grade what the account can see against what the org holds. Pure."""
    if total is None or visible is None:
        return "unknown"
    if total <= 0:
        return "nothing-to-cover"
    if visible >= total:
        return "full"
    if visible == 0 or visible * 20 < total:
        return "collapsed"
    if visible * 2 < total:
        return "shrunken"
    return "partial"


def drift(expected, actual):
    """Compare the configured base permission against the live one. Pure."""
    if not expected or actual is None:
        return ("drift-unknown",
                "no expected base permission was supplied, or the live one "
                "could not be read, so there is nothing to compare.")
    want, have = base_rank(expected), base_rank(actual)
    if want < 0 or have < 0:
        return ("drift-unknown",
                "one of the two values is not a documented base permission.")
    if want == have:
        return ("base-unchanged",
                "the organization still reports the base permission this "
                "integration was configured against.")
    if have < want:
        return ("base-tightened",
                "configured for %r, the organization now says %r. That is one "
                "field and it re-graded every repository at once."
                % (expected, actual))
    return ("base-loosened",
            "configured for %r, the organization now says %r, which grants "
            "more implicit access than you expected rather than less."
            % (expected, actual))


def verdict(base, coverage):
    """The finding, in one state. Pure. (state, detail)."""
    if base is None:
        return ("base-unreadable",
                "the base permission could not be read, so the coverage number "
                "stands on its own. Read it with a token that has organization "
                "access before concluding anything about the default.")
    if base == "none" and coverage in ("collapsed", "shrunken"):
        return ("base-none-implicit-access-gone",
                "base permission is none and this account reaches a fraction "
                "of the organization. The repositories it still reaches are "
                "the ones it was added to explicitly; the rest were never "
                "granted, only defaulted.")
    if base == "none" and coverage in ("full", "partial"):
        return ("base-none-explicit-grants-hold",
                "base permission is none and coverage is largely intact, which "
                "means this account's access is explicit. It is not exposed to "
                "this change.")
    if base != "none" and coverage in ("collapsed", "shrunken"):
        return ("coverage-lost-elsewhere",
                "the base permission still grants implicit access and the "
                "coverage is short anyway, so the loss is not this field. "
                "Membership, SSO authorization and an App's repository "
                "selection are the other ways a list gets shorter.")
    if coverage == "nothing-to-cover":
        return ("nothing-to-cover",
                "the organization reports no repositories, so there is no "
                "coverage question to answer.")
    return ("coverage-as-expected",
            "the account reaches what the base permission implies it should. "
            "Nothing here explains a shorter list.")


def repair(state, org):
    """The narrow repair. Pure. Nothing here is executed."""
    if state == "base-none-implicit-access-gone":
        return ("add this account, or a team it belongs to, to the "
                "repositories the job is meant to cover in %s. Do not raise "
                "the base permission back: that re-grants implicit access to "
                "every member of the organization to fix one integration."
                % org)
    if state == "coverage-lost-elsewhere":
        return ("look past the base permission. Check that the account is "
                "still a member, that the token is SSO-authorized where that "
                "applies, and, for a GitHub App, that the installation covers "
                "the repositories you expect.")
    if state == "base-unreadable":
        return ("re-read the organization with a token that has organization "
                "access. Until then the coverage number is a measurement "
                "without an explanation.")
    if state == "base-none-explicit-grants-hold":
        return ("nothing. Keep it that way: explicit grants are what makes "
                "this account immune to the next change to the default.")
    return ("nothing on the base permission. The shorter list, if there is "
            "one, has another cause.")


def get(session, path):
    """One GET. Returns the response object."""
    return session.get(API + path, timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("org", help="the organization whose repositories shrank")
    parser.add_argument("--expect",
                        help="the base permission this integration was "
                             "configured against, e.g. read")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    log.info("read cost: %d request(s) against the core hourly quota", read_cost())

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    org_response = get(session, "/orgs/" + args.org)
    org_payload = org_response.json() if org_response.status_code == 200 else {}
    base, base_detail = base_state(org_payload)
    log.info("base permission: %s — %s", base or "unreadable", base_detail)

    drift_state, drift_detail = drift(args.expect, base)
    log.info("drift: %s — %s", drift_state, drift_detail)

    mine = get(session, "/user/repos?affiliation=organization_member&per_page=1")
    body = mine.json() if mine.status_code == 200 else []
    visible, how = count_from_link(mine.headers.get("Link"),
                                   len(body) if isinstance(body, list) else 0)
    log.info("visible through membership: %s (%s)", visible, how)

    total, total_detail = org_total(org_payload)
    log.info("organization holds: %s repositories (%s)",
             "unreadable" if total is None else total, total_detail)

    also = get(session, "/orgs/%s/repos?per_page=1" % args.org)
    also_body = also.json() if also.status_code == 200 else []
    listed, listed_how = count_from_link(
        also.headers.get("Link"), len(also_body) if isinstance(also_body, list) else 0)
    log.info("listed by /orgs/%s/repos: %s (%s)", args.org, listed, listed_how)

    coverage = coverage_state(visible, total)
    log.info("coverage: %s — %s of %s", coverage, visible,
             "unreadable" if total is None else total)

    state, detail = verdict(base, coverage)
    log.info("state: %s — %s", state, detail)
    log.info("repair: %s", repair(state, args.org))

    print(json.dumps({
        "organization": args.org,
        "default_repository_permission": base,
        "expected_base_permission": args.expect,
        "drift_state": drift_state,
        "visible_through_membership": visible,
        "visible_source": how,
        "listed_by_org_repos": listed,
        "organization_total": total,
        "coverage": coverage,
        "state": state,
        "detail": detail,
        "repair": repair(state, args.org),
    }, indent=2, default=str))
    return 1 if state in ("base-none-implicit-access-gone",
                          "coverage-lost-elsewhere") else 0


if __name__ == "__main__":
    sys.exit(main())
