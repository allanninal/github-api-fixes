"""Find organizations that GitHub withheld from a 200 because of SAML SSO.

Read only. GET requests and nothing else: read:org is enough. The repair is
printed, never performed, because this script holds a credential that spans
several organizations.
"""
import argparse
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_sso_partial_results")

API = "https://api.github.com"
UA = "github-sso-partial-results/1.0"


def parse_sso(value):
    """Parse an X-GitHub-SSO header value. Pure, so both forms are testable.

    Two shapes exist. On a 200 the partial form names the organizations that were
    withheld from the body:

        partial-results; organizations=21955855,20582480

    On a 403 the required form carries the URL that authorizes the token:

        required; url=https://github.com/orgs/acme/sso?authorization_request=...

    A value that matches neither is reported as "unknown" rather than folded into
    "none". A header nobody parsed is still a header GitHub sent, and reading it
    as absence is exactly how a partial answer becomes a clean bill of health.
    """
    raw = (value or "").strip()
    if not raw:
        return {"kind": "none", "organizations": [], "url": None}

    parts = [p.strip() for p in raw.split(";") if p.strip()]
    kind = parts[0].lower()
    orgs, url = [], None
    for part in parts[1:]:
        name, sep, val = part.partition("=")
        if not sep:
            continue
        name = name.strip().lower()
        if name == "organizations":
            orgs = [o.strip() for o in val.split(",") if o.strip()]
        elif name == "url":
            url = val.strip()

    if kind not in ("partial-results", "required"):
        kind = "unknown"
    return {"kind": kind, "organizations": orgs, "url": url}


def verdict(status, sso, listed):
    """Decide what one response means. Pure. Returns (state, detail).

    The header outranks the status code: a 200 carrying partial-results is a
    failure and a 403 carrying required is at least an honest one.
    """
    kind = sso.get("kind")

    if kind == "partial-results":
        hidden = sso.get("organizations") or []
        return ("partial",
                "%d organization(s) in the body and %d withheld (%s). The status "
                "is 200 and the JSON is valid; the answer is not."
                % (listed, len(hidden), ", ".join(hidden) or "unnamed"))

    if kind == "required":
        return ("authorization-required",
                "the token is not SSO-authorized and GitHub said so out loud. "
                "Authorize it at %s" % (sso.get("url") or "the org's SSO page",))

    if kind == "unknown":
        return ("unreadable",
                "an X-GitHub-SSO header was sent and this parser did not "
                "understand it. Treat that as partial, never as clean, and read "
                "the raw value before trusting the list.")

    if status == 403:
        return ("forbidden",
                "403 with no X-GitHub-SSO header, so this is not SSO. Look at "
                "org OAuth app restrictions, an IP allow list, or a missing "
                "read:org scope instead.")
    if status != 200:
        return ("unexpected", "HTTP %s" % (status,))

    return ("complete", "%d organization(s), no partial-results header" % (listed,))


def get(session, url, **params):
    return session.get(url, params=params, timeout=30)


def list_orgs(session, api):
    """Page /user/orgs, returning (organizations, worst response seen).

    The header is attached per response, so every page is inspected. The first
    page carrying a partial-results header wins, because one hole makes the
    whole list partial.
    """
    orgs = []
    finding = {"status": 200, "sso": {"kind": "none", "organizations": [], "url": None}}
    page = 1
    while True:
        r = get(session, api + "/user/orgs", per_page=100, page=page)
        sso = parse_sso(r.headers.get("x-github-sso"))
        if sso["kind"] != "none" and finding["sso"]["kind"] == "none":
            finding = {"status": r.status_code, "sso": sso}
        if r.status_code != 200:
            finding["status"] = r.status_code
            break
        items = r.json()
        orgs.extend(items)
        if len(items) < 100:
            break
        page += 1
    return orgs, finding


def resolve(session, api, org_id):
    """Turn a withheld organization ID into a login, or admit it cannot."""
    r = get(session, "%s/organizations/%s" % (api, org_id))
    if r.status_code != 200:
        return None
    return r.json().get("login")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--api", default=API,
                    help="API host, for GitHub Enterprise Server")
    ap.add_argument("--resolve-ids", action="store_true",
                    help="one extra GET per withheld organization, to name it")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (read:org is enough)")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    orgs, finding = list_orgs(session, args.api)
    state, detail = verdict(finding["status"], finding["sso"], len(orgs))

    if state == "complete":
        log.info("%-22s %s", state, detail)
        return 0

    log.warning("%-22s %s", state, detail)
    log.warning("  visible: %s",
                ", ".join(str(o.get("login")) for o in orgs) or "none")

    if args.resolve_ids and finding["sso"]["kind"] == "partial-results":
        for org_id in finding["sso"]["organizations"]:
            name = resolve(session, args.api, org_id)
            log.warning("  withheld: %s (%s)", org_id,
                        name or "could not be resolved with this token either")

    log.warning("  repair: authorize this token for the withheld organizations "
                "in your GitHub settings under SSO, or run one credential per "
                "organization and stop asking a single token a question it "
                "cannot answer completely.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
