"""Say which layer produced a 401, using two messages and one control request.

Read only. Three GETs: the REST root with the credential attached, the REST root
with no credential at all, and GET /user. None of them writes and none of them
needs a scope.

GitHub returns two different 401 messages. "Bad credentials" means a value was
received and refused. "Requires authentication" means nothing was received. The
distance between those two sentences is the whole diagnosis, and the control
request is what makes the first one provable rather than assumed.
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_401_provenance")

API = "https://api.github.com"
UA = "github-401-provenance/1.0"

# The REST root. Any anonymous caller may read it, which is precisely why
# attaching a broken credential to it is such a clean experiment.
PUBLIC_PATH = "/"

BAD_CREDENTIALS = "bad credentials"
REQUIRES_AUTH = "requires authentication"

# Response furniture GitHub puts on everything it answers. An appliance in the
# middle that decides to return 401 will not have any of it.
GITHUB_FURNITURE = ("x-github-request-id", "x-github-media-type",
                    "x-github-api-version-selected")


def message_of(body):
    """The message GitHub put in the body, folded to lower case. Pure.

    None for anything that is not a JSON object with a non-empty message, so a
    truncated body or an HTML error page from a proxy does not get read as a
    GitHub verdict.
    """
    if not isinstance(body, dict):
        return None
    value = body.get("message")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().lower()


def from_github(headers):
    """Whether GitHub itself answered, rather than something in front of it. Pure.

    Returns (bool, which-header-said-so). The negative result is the valuable
    one: a 401 with none of this furniture is very likely a proxy, and no amount
    of rotating credentials will change its mind.
    """
    lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
    for name in GITHUB_FURNITURE:
        if lowered.get(name):
            return (True, name)
    if "github" in str(lowered.get("server", "")).lower():
        return (True, "server")
    return (False, None)


def rung(status, message):
    """Reduce one probe to a symbol. Pure.

    The two 401s get different symbols, because they are different findings, and
    a 401 with neither message gets a third symbol rather than being forced into
    whichever of the two the code happened to check first.
    """
    try:
        status = int(status)
    except (TypeError, ValueError):
        return "error"
    if status == 0:
        return "error"
    if 200 <= status < 300:
        return "ok"
    if status == 401:
        if message == BAD_CREDENTIALS:
            return "rejected"
        if message == REQUIRES_AUTH:
            return "anonymous"
        return "unlabelled-401"
    if status == 403:
        return "forbidden"
    return "http-%d" % status


def diagnose(public_with, public_without, user, expected_login=None):
    """Name the layer that produced the 401. Pure.

    Each probe is a dict of {"status", "message", "github", "login"}. Nothing
    here looks at the credential's text: the argument is entirely about what
    GitHub did with it.
    """
    def symbol(probe):
        probe = probe or {}
        return rung(probe.get("status"), probe.get("message"))

    with_header = symbol(public_with)
    without_header = symbol(public_without)
    identity = symbol(user)

    if with_header in ("rejected", "anonymous", "unlabelled-401") \
            and not (public_with or {}).get("github"):
        return ("not-github",
                "the 401 carried none of GitHub's response furniture: no "
                "request id, no media type, no GitHub server header. Something "
                "between this process and api.github.com answered, and it is "
                "not looking at your credential. Re-minting will not help.")

    if without_header == "error":
        return ("no-baseline",
                "the control request, which carries no credential at all, could "
                "not be made. Without it nothing below can be separated from a "
                "network fault.")

    if without_header != "ok":
        return ("anonymous-refused",
                "the control request carries no credential and was still "
                "refused (%s). Whatever is producing this is not reading your "
                "token: look at IP allow lists, egress proxies and the network "
                "before you look at the credential." % without_header)

    if with_header == "rejected":
        return ("credential-rejected",
                "GitHub parsed the value and refused it. An endpoint that needs "
                "no credential at all answered 200 without the header and 401 "
                "with it, so the value being sent is the thing being rejected: "
                "expired, revoked, truncated, or from an account that no longer "
                "exists. That is a re-mint, not a network change.")

    if identity == "anonymous":
        return ("header-not-arriving",
                "GET /user answered 401 Requires authentication, which is the "
                "message for a request that carried nothing. The header is "
                "being lost between here and GitHub: a redirect that dropped "
                "it, a client that only applies auth to configured hosts, or a "
                "proxy that strips what it does not recognise.")

    if identity == "rejected" and with_header == "ok":
        return ("path-dependent",
                "the public endpoint accepted or ignored the same credential "
                "that GET /user refused. Two requests from the same process are "
                "not arriving as the same request, which points at something "
                "rewriting them in between.")

    if identity == "rejected":
        return ("credential-rejected",
                "GET /user answered 401 Bad credentials, so the value was "
                "received and refused.")

    if identity == "forbidden":
        return ("authenticated-but-forbidden",
                "the credential is valid and GET /user answered 403. That is "
                "not a bad credential: look at SSO authorisation, IP allow "
                "lists and organization policy.")

    if identity == "ok":
        login = (user or {}).get("login")
        if expected_login and str(login or "").lower() != str(expected_login).lower():
            return ("wrong-account",
                    "the credential is valid and belongs to %r, not to the "
                    "expected %r. A valid token for the wrong identity produces "
                    "404s and 403s all over an integration and never once says "
                    "the word credentials." % (login, expected_login))
        return ("credential-valid",
                "the credential authenticates as %r. Whatever is returning 401 "
                "is not this credential on this host, so look at the other "
                "variable, the other host, or the other process."
                % (login or "an unnamed account"))

    return ("unclear",
            "the three probes do not agree: root with header %s, root without "
            "header %s, /user %s. Report the request id from the failing "
            "response rather than guessing."
            % (with_header, without_header, identity))


def probe(path, token=None):
    """One GET, reduced to the four things the diagnosis needs."""
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": UA}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        response = requests.get(API + path, headers=headers, timeout=30)
    except requests.RequestException as exc:
        log.error("GET %s failed: %s", path, exc)
        return {"status": 0, "message": None, "github": False, "login": None,
                "request_id": None}
    try:
        body = response.json()
    except ValueError:
        body = None
    is_github, which = from_github(response.headers)
    return {"status": response.status_code,
            "message": message_of(body),
            "github": is_github,
            "github_signal": which,
            "login": (body or {}).get("login") if isinstance(body, dict) else None,
            "request_id": response.headers.get("x-github-request-id")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="GITHUB_TOKEN",
                        help="environment variable holding the credential")
    parser.add_argument("--expect-login",
                        help="the account this credential is supposed to be")
    args = parser.parse_args()

    token = os.environ.get(args.env)
    if not token:
        log.error("%s is not set, so there is no credential to account for. "
                  "That is a different note: every request goes out anonymous.",
                  args.env)
        return 2

    public_with = probe(PUBLIC_PATH, token)
    public_without = probe(PUBLIC_PATH, None)
    user = probe("/user", token)

    log.info("public endpoint with the header:    %d %s",
             public_with["status"], public_with["message"] or "")
    log.info("public endpoint without any header: %d %s",
             public_without["status"], public_without["message"] or "")
    log.info("GET /user:                          %d %s",
             user["status"], user["message"] or "")
    if not public_with["github"]:
        log.warning("the credentialled response carried none of GitHub's "
                    "response furniture")
    for name, result in (("root", public_with), ("user", user)):
        if result["request_id"]:
            log.info("%s request id %s", name, result["request_id"])

    state, detail = diagnose(public_with, public_without, user, args.expect_login)
    log.info("%s: %s", state, detail)

    if state == "credential-rejected":
        log.info("repair: re-mint the credential, store it with no surrounding "
                 "whitespace or quotes, and assert at startup that GET /user "
                 "returns 200 before doing any real work.")
    if state == "header-not-arriving":
        log.info("repair: log the outgoing request headers at the transport "
                 "layer and check the tier as well; a stripped header means "
                 "60 requests an hour, not zero.")
    if state == "wrong-account":
        log.info("repair: assert the expected login at startup. It is three "
                 "lines and it costs one free request.")

    print(json.dumps({"state": state,
                      "public_with": public_with["status"],
                      "public_without": public_without["status"],
                      "user": user["status"]}, indent=2))
    return 1 if state not in ("credential-valid",) else 0


if __name__ == "__main__":
    sys.exit(main())
