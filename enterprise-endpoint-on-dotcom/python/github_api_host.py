"""Say which GitHub installation this client is actually talking to.

Read only, and two of its three calls need no credential at all. Nothing is
configured, set or written: the repair is an environment variable and a
startup assertion, and both are printed.

The point of the note: github.com, a GitHub Enterprise Server appliance and an
Enterprise Cloud tenant with data residency are separate installations. A
credential from one is meaningless at the others, and a base URL that defaults
to the wrong one produces a 404 on every route or a flat 401 on a token minted
minutes ago.

What this can and cannot see: /meta reports installed_version on Enterprise
Server, which is the cleanest discriminator available. It cannot separate an
Enterprise Cloud organization from a personal account, because both are served
from api.github.com, and that is not a host problem.

Environment:

    GITHUB_TOKEN    optional. Needed only for the identity assertion.
"""
import argparse
import json
import logging
import os
import sys
from urllib.parse import urlparse

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_api_host")

UA = "github-api-host/1.0"

DOTCOM_API_HOST = "api.github.com"
DOTCOM_WEB_HOST = "github.com"
GHES_REST_SUFFIX = "/api/v3"
GHES_GRAPHQL_SUFFIX = "/api/graphql"
RESIDENCY_SUFFIX = ".ghe.com"

FAMILIES = ("dotcom", "enterprise-server", "enterprise-cloud-data-residency",
            "web-host-not-api", "unknown")


def read_cost(with_identity):
    """(requests, unauthenticated ones) this run will spend. Pure.

    The second number is the point: the host check needs no credential, so it
    can run at process start before any secret has been read.
    """
    made = 2 + (1 if with_identity else 0)
    return (made, 2)


def normalise_base(url):
    """Trim a base URL to a comparable form. Pure."""
    value = str(url or "").strip()
    while value.endswith("/"):
        value = value[:-1]
    return value


def host_of(url):
    """The hostname in a URL, lowercased, or None. Pure."""
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return None
    return (parsed.hostname or "").lower() or None


def family_from_url(base):
    """Guess the installation family from the configured URL. Pure.

    A guess, explicitly. It is compared against what the host itself reports,
    and the disagreement between the two is more interesting than either.
    """
    value = normalise_base(base)
    host = host_of(value)
    if not host:
        return ("unknown", "no host could be parsed out of the base URL.")
    if host == DOTCOM_API_HOST:
        return ("dotcom", "api.github.com is the github.com API host.")
    if host == DOTCOM_WEB_HOST:
        return ("web-host-not-api",
                "github.com is the web interface. The API lives at "
                "api.github.com, and a client pointed here will be handed HTML.")
    if host.startswith("api.") and host.endswith(RESIDENCY_SUFFIX):
        return ("enterprise-cloud-data-residency",
                "an api.SUBDOMAIN.ghe.com host is an Enterprise Cloud tenant "
                "with data residency, which is its own installation.")
    if value.endswith(GHES_REST_SUFFIX):
        return ("enterprise-server",
                "a host with the %s suffix is an Enterprise Server appliance."
                % GHES_REST_SUFFIX)
    if value.endswith(GHES_GRAPHQL_SUFFIX):
        return ("enterprise-server",
                "this is the appliance's GraphQL path; its REST base is %s."
                % GHES_REST_SUFFIX)
    return ("web-host-not-api",
            "this host carries no API prefix. On an appliance the REST base is "
            "the hostname plus %s, and without it you are talking to the web "
            "interface, which answers 200 and sends HTML." % GHES_REST_SUFFIX)


def content_is_html(content_type):
    """Did the host send a web page. Pure."""
    return "html" in str(content_type or "").lower()


def family_from_meta(status, content_type, body):
    """What the host itself says it is. Pure. (family, detail).

    installed_version is the discriminator: the Enterprise Server schema for
    /meta carries it and the github.com one does not.
    """
    if content_is_html(content_type):
        return ("web-host-not-api",
                "the host returned HTML rather than JSON, so this is a web "
                "interface. A client checking only the status code sees a 200 "
                "here and reports success.")
    if int(status or 0) != 200 or not isinstance(body, dict):
        return ("meta-unreadable",
                "/meta did not return a readable JSON document, so the host "
                "could not identify itself. On a private appliance this "
                "endpoint can require authentication.")
    version = body.get("installed_version")
    if version:
        return ("enterprise-server",
                "installed_version is present (%s), which the github.com "
                "schema for this endpoint does not carry." % version)
    if "verifiable_password_authentication" in body or "hooks" in body:
        return ("dotcom-or-enterprise-cloud",
                "a valid /meta document with no installed_version. That is "
                "github.com, or an Enterprise Cloud tenant, which are served "
                "from the same host and cannot be separated here.")
    return ("meta-unreadable",
            "the document does not look like /meta, so nothing can be "
            "concluded from it.")


def served_host_from_root(root):
    """The host named in the root endpoint map. Pure. (host, detail).

    The URLs in that map are absolute, so this is the host that actually
    answered rather than the one you dialled. It is the only reading here that
    survives a redirect or a proxy.
    """
    if not isinstance(root, dict) or not root:
        return (None,
                "the root endpoint map was not readable, so the host that "
                "answered cannot be named.")
    for key in ("current_user_url", "repository_url", "user_url"):
        value = root.get(key)
        host = host_of(value) if isinstance(value, str) else None
        if host:
            return (host, "taken from %s in the root map." % key)
    for value in root.values():
        host = host_of(value) if isinstance(value, str) else None
        if host:
            return (host, "taken from an absolute URL in the root map.")
    return (None, "the root map carried no absolute URL to read a host from.")


def agreement(guessed, reported, configured_host, served_host):
    """Compare three independent readings. Pure. (state, detail)."""
    if reported == "web-host-not-api" or guessed == "web-host-not-api":
        return ("no-api-prefix",
                "this is a web interface rather than an API base. On an "
                "appliance append %s to the hostname; on github.com use "
                "api.github.com." % GHES_REST_SUFFIX)
    if served_host and configured_host and served_host != configured_host:
        return ("served-elsewhere",
                "you dialled %s and %s answered. A redirect or a proxy is "
                "sending this client somewhere else, which reading the "
                "configuration would never have caught."
                % (configured_host, served_host))
    if reported == "meta-unreadable":
        return ("host-unidentified",
                "the host did not identify itself, so the family in the URL is "
                "the only evidence and it is a guess.")
    if reported == "enterprise-server" and guessed != "enterprise-server":
        return ("wrong-host-family",
                "the URL looks like %s and the host reports itself as an "
                "Enterprise Server appliance. Those are different "
                "installations." % guessed)
    if reported == "dotcom-or-enterprise-cloud" and guessed == "enterprise-server":
        return ("wrong-host-family",
                "the URL carries an appliance suffix and the host answering is "
                "not an appliance. Those are different installations.")
    return ("agrees",
            "the family guessed from the URL, the family the host reports, and "
            "the host that actually answered are all the same installation.")


def identity_check(status, login, html_url, expected_login, served_host):
    """Assert the account, because the token cannot be checked locally. Pure."""
    code = int(status or 0)
    if code == 0:
        return ("not-checked",
                "no identity call was made, so nothing confirms the credential "
                "belongs to this installation.")
    if code == 401:
        return ("credential-not-of-this-host",
                "the credential was rejected outright by this host. A token "
                "minted at a different installation is not a weak token here, "
                "it is not a token at all.")
    if code != 200:
        return ("identity-unreadable",
                "HTTP %s from the identity call, so the account could not be "
                "read." % status)
    url_host = host_of(html_url)
    if expected_login and str(login or "").lower() != str(expected_login).lower():
        return ("wrong-account",
                "this host knows the credential as %r and you expected %r. "
                "Same shape of secret, different installation."
                % (login, expected_login))
    if url_host and served_host and url_host != served_host \
            and not url_host.endswith(served_host) \
            and not served_host.endswith(url_host):
        return ("html-url-host-mismatch",
                "the account's html_url points at %s while %s answered, which "
                "is worth explaining before trusting either."
                % (url_host, served_host))
    return ("identity-as-expected",
            "the account this host returns is the one you expected.")


def token_shape_is_no_evidence(token):
    """State plainly that a prefix cannot name an installation. Pure."""
    value = (token or "").strip()
    known = ("github_pat_", "ghp_", "gho_", "ghu_", "ghs_", "ghr_")
    if any(value.startswith(prefix) for prefix in known):
        return ("class-known-host-unknown",
                "the prefix names the credential class and never the "
                "installation that issued it. There is no local test for which "
                "host a token belongs to; the identity call is the only one.")
    return ("class-unknown",
            "the credential class could not be named, and it would not have "
            "named the installation anyway.")


def verdict(agreement_state, identity_state):
    """The finding, in one state. Pure. (state, detail)."""
    if agreement_state == "no-api-prefix":
        return ("no-api-prefix",
                "the base URL is a web interface, so every API call is being "
                "answered with a web page.")
    if agreement_state == "wrong-host-family":
        return ("wrong-installation",
                "the client is configured for one installation and talking to "
                "another. Every 404 and every 401 follows from that.")
    if agreement_state == "served-elsewhere":
        return ("redirected-elsewhere",
                "the host that answered is not the host that was dialled, so "
                "the configuration is not the whole story.")
    if identity_state in ("credential-not-of-this-host", "wrong-account"):
        return ("credential-from-another-host",
                "the host is reachable and the credential does not belong to "
                "it, which is the same bug seen from the other side.")
    if agreement_state == "host-unidentified":
        return ("host-unidentified",
                "the host would not identify itself, so this run narrows the "
                "question rather than answering it.")
    if identity_state == "html-url-host-mismatch":
        return ("host-mismatch-in-payload",
                "the objects this host returns point at a different hostname "
                "from the one serving them.")
    return ("host-as-configured",
            "the base URL, the host and the account all describe the same "
            "installation.")


def repair(state, base):
    """The sentence a reader has to act on. Pure. Nothing here is configured."""
    if state == "no-api-prefix":
        return ("set the API base URL properly: %s for github.com, the "
                "appliance hostname plus %s for Enterprise Server, and "
                "api.SUBDOMAIN.ghe.com for a data-residency tenant."
                % ("https://" + DOTCOM_API_HOST, GHES_REST_SUFFIX))
    if state in ("wrong-installation", "credential-from-another-host"):
        return ("set the base URL explicitly for this environment rather than "
                "letting a library default decide, and pair each base URL with "
                "the credential minted at that installation. %s is not the "
                "host holding these resources." % (base or "the configured base"))
    if state == "redirected-elsewhere":
        return ("find out what is redirecting this client. Then assert at "
                "startup that the host in the root map matches the host you "
                "configured, so the next one is caught in a second.")
    if state == "host-unidentified":
        return ("re-run with a credential this host accepts, or from a network "
                "that can reach it. A private appliance can require "
                "authentication even for /meta.")
    return ("nothing to change. Keep this as a startup assertion rather than a "
            "thing somebody runs after a week of 404s.")


def get(session, url):
    """One GET. Returns the response object, or None if the host is unreachable."""
    try:
        return session.get(url, timeout=30)
    except requests.RequestException as err:
        log.warning("%s did not answer: %s", url, err)
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.environ.get("GITHUB_API_URL")
                        or "https://" + DOTCOM_API_HOST,
                        help="the base URL the client resolved at startup")
    parser.add_argument("--expect-login",
                        help="the account this credential should be, on this host")
    args = parser.parse_args()

    base = normalise_base(args.base)
    token = os.environ.get("GITHUB_TOKEN")
    made, free = read_cost(bool(token))
    log.info("read cost: %d REST request(s), %d of them unauthenticated and free",
             made, free)

    configured_host = host_of(base)
    guessed, guessed_detail = family_from_url(base)
    log.info("configured: host %s, guessed family %s. %s", configured_host,
             guessed, guessed_detail)

    session = requests.Session()
    session.headers.update({
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub refuses requests with no User-Agent before it looks at auth.
        "User-Agent": UA,
    })

    meta = get(session, base + "/meta")
    if meta is None:
        reported, reported_detail = ("meta-unreadable", "the host did not answer.")
        meta_body, content_type = None, ""
    else:
        content_type = meta.headers.get("content-type", "")
        try:
            meta_body = meta.json()
        except ValueError:
            meta_body = None
        reported, reported_detail = family_from_meta(meta.status_code,
                                                     content_type, meta_body)
    log.info("meta: %s. %s", reported, reported_detail)

    root = get(session, base + "/")
    root_body = None
    if root is not None:
        try:
            root_body = root.json()
        except ValueError:
            root_body = None
    served_host, served_detail = served_host_from_root(root_body)
    log.info("served host: %s (%s)", served_host or "unknown", served_detail)

    agreement_state, agreement_detail = agreement(guessed, reported,
                                                  configured_host, served_host)
    log.info("%s: %s", agreement_state, agreement_detail)

    identity_state, identity_detail = ("not-checked", "no token supplied.")
    login = None
    if token:
        session.headers["Authorization"] = "Bearer " + token
        who = get(session, base + "/user")
        if who is not None:
            body = {}
            try:
                body = who.json() or {}
            except ValueError:
                body = {}
            login = body.get("login")
            identity_state, identity_detail = identity_check(
                who.status_code, login, body.get("html_url"),
                args.expect_login, served_host)
        shape_state, shape_detail = token_shape_is_no_evidence(token)
        log.info("%s: %s", shape_state, shape_detail)
    log.info("identity: %s. %s", identity_state, identity_detail)

    state, detail = verdict(agreement_state, identity_state)
    log.info("%s: %s", state, detail)
    fix = repair(state, base)
    log.info("repair: %s", fix)

    print(json.dumps({
        "base": base,
        "configured_host": configured_host,
        "guessed_family": guessed,
        "reported_family": reported,
        "served_host": served_host,
        "agreement_state": agreement_state,
        "identity_state": identity_state,
        "login": login,
        "state": state,
        "detail": detail,
        "repair": fix,
    }, indent=2, default=str))
    return 1 if state != "host-as-configured" else 0


if __name__ == "__main__":
    sys.exit(main())
