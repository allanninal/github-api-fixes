"""Tell an individual OAuth revocation apart from an application wide one.

Read only. One GET /user per stored user token, which is the cheapest call
that answers "is this credential still accepted, and whose is it". Nothing here
mints, refreshes or revokes anything; the repair is a URL that is printed for
the affected person to open.

The reading is a population, not a request. One refusal among many successes is
that user's revocation. Every refusal at once is the application: a rotated
client secret, a suspended app, or an organization owner removing the approval
for a whole cohort. No single response can tell those apart.

The definitive per-token check lives at /applications/{client_id}/token, which
is a write shaped call needing the client secret. This section's scripts do not
hold application secrets and do not make that call.
"""
import argparse
import json
import logging
import os
import sys
from urllib.parse import urlencode

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_user_token_liveness")

API = "https://api.github.com"
UA = "github-user-token-liveness/1.0 (+https://example.com/contact)"
AUTHORIZE = "https://github.com/login/oauth/authorize"


def collect_tokens(environ, prefix):
    """Gather stored user tokens out of a mapping by prefix. Pure.

    Values leave here only to be sent. Everything the report prints is the
    variable name and the login, which are the two things a person can act on
    and neither of which is a secret.
    """
    return sorted((name, value) for name, value in environ.items()
                  if name.startswith(prefix) and value)


def token_result(status, message=None):
    """Classify one liveness probe. Pure.

    Four states, because a 403 is not a revocation: it is an account or an
    organization refusing something the credential is otherwise entitled to
    present, and it does not mean the authorization is gone.
    """
    if status == 200:
        return "alive"
    if status == 401:
        return "rejected"
    if status == 403:
        return "forbidden"
    return "error"


def population_verdict(results):
    """Read the fleet rather than the request. Pure.

    results: [(name, state), ...]. The counts are the diagnosis; no individual
    response contains this information.
    """
    if not results:
        return ("no-tokens",
                "nothing was collected, so there is nothing to read. Check the "
                "prefix the variables are named with.")
    alive = [n for n, s in results if s == "alive"]
    rejected = [n for n, s in results if s == "rejected"]
    if not rejected:
        return ("all-healthy",
                "every stored token is accepted, so no authorization has been "
                "revoked. Whatever you are chasing is somewhere else.")
    if len(results) == 1:
        return ("single-token-inconclusive",
                "one token is stored and it is refused. That is consistent "
                "with this user revoking, and equally consistent with the "
                "application being suspended or its secret rotated. With one "
                "sample the two cannot be separated.")
    if alive:
        return ("individual-revocation",
                "%d of %d stored tokens are refused while others work, so this "
                "is those people's decision rather than an application "
                "problem: %s" % (len(rejected), len(results),
                                 ", ".join(rejected)))
    return ("application-wide",
            "all %d stored tokens are refused at once. Users do not coordinate "
            "revocations. Look at the application: a rotated client secret, a "
            "suspended app, or an organization owner removing the approval for "
            "the whole cohort." % len(results))


def retry_disposition(state):
    """Say whether a state should ever be retried. Pure.

    The operationally valuable half of this note. A revoked user token does not
    recover, so a schedule that keeps trying turns one broken connection into a
    permanent stream of refusals competing with the users who still work.
    """
    if state == "rejected":
        return ("terminal",
                "a revoked or invalid user token never recovers on its own. "
                "Mark the connection broken, take it off the schedule, and ask "
                "the person to authorize again.")
    if state == "forbidden":
        return ("terminal",
                "the credential was accepted and the action was refused. "
                "Retrying changes nothing; this is an access question.")
    if state == "error":
        return ("retryable",
                "the probe itself did not complete, so nothing is known about "
                "the credential. This one is worth trying again.")
    return ("none", "nothing to retry.")


def authorize_url(client_id, scopes=(), redirect_uri=None, state=None):
    """Build the URL that starts the authorization flow again. Pure.

    This is the whole repair for an individual revocation: there is nothing to
    fix on your side, only a person to ask.
    """
    params = [("client_id", client_id)]
    if scopes:
        params.append(("scope", " ".join(scopes)))
    if redirect_uri:
        params.append(("redirect_uri", redirect_uri))
    if state:
        params.append(("state", state))
    return AUTHORIZE + "?" + urlencode(params)


def probe(token):
    """One GET /user. Returns (status, login, message)."""
    response = requests.get(API + "/user", timeout=30, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        return response.status_code, body.get("login"), body.get("message")
    return response.status_code, None, None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env-prefix", default="GH_USER_TOKEN_",
                    help="collect every environment variable with this prefix")
    ap.add_argument("--scopes", default="",
                    help="space separated scopes for the printed authorize URL")
    args = ap.parse_args()

    stored = collect_tokens(os.environ, args.env_prefix)
    if not stored:
        log.error("no variables found with the prefix %s. Store one token per "
                  "connection so the set can be read as a set", args.env_prefix)
        return 2

    client_id = os.environ.get("GITHUB_OAUTH_CLIENT_ID", "")
    results, findings = [], []
    for name, token in stored:
        status, login, message = probe(token)
        state = token_result(status, message)
        results.append((name, state))
        log.info("%-24s %-9s %s", name, state, login or "-")
        findings.append({"env": name, "state": state, "login": login,
                         "status": status})

    verdict, detail = population_verdict(results)
    log.info("%s: %s", verdict, detail)

    for name, state in results:
        if state == "alive":
            continue
        disposition, why = retry_disposition(state)
        log.info("%s: %s. %s", name, disposition, why)

    if verdict in ("individual-revocation", "single-token-inconclusive"):
        if client_id:
            url = authorize_url(client_id,
                                args.scopes.split() if args.scopes else ())
            log.info("repair: send the affected people through the flow again: "
                     "%s", url)
        else:
            log.info("repair: set GITHUB_OAUTH_CLIENT_ID to have the authorize "
                     "URL printed here.")
    if verdict == "application-wide":
        log.info("repair: this is not the users. Check whether the client "
                 "secret was rotated, whether the application is suspended, "
                 "and whether an organization owner removed its approval.")

    print(json.dumps({"verdict": verdict, "tokens": findings}, indent=2))
    return 1 if verdict != "all-healthy" else 0


if __name__ == "__main__":
    sys.exit(main())
