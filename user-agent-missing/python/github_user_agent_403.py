"""Sort a GitHub 403 by cause, then grade the User-Agent the client sent.

Read only. One GET, to a path you choose, defaulting to the REST root, which
any anonymous caller may read. Nothing here writes, nothing needs a scope, and
the credential is optional because the rule this note is about is applied
before the credential is looked at.

GitHub requires a User-Agent header on every API request. Requests without one
are refused with a 403 whose body names the rule. That makes it the only 403 in
this API you can identify from a single response, and the easiest one to
mistake for the other three, all of which produce the same status code.
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_user_agent_403")

API = "https://api.github.com"

# The User-Agent strings HTTP clients supply when nobody sets one. They satisfy
# the rule, so the request works. They also describe several million other
# callers, which is the half of the ask they fail: the documented request is
# for a header naming the application or the account behind it.
LIBRARY_DEFAULTS = (
    "python-requests/", "python-urllib/", "urllib3/", "python-httpx/", "httpx/",
    "go-http-client/", "node-fetch/", "undici", "node/", "axios/", "got (",
    "okhttp/", "java/", "apache-httpclient/", "curl/", "libcurl/", "wget/",
    "httpie/", "postmanruntime/", "restsharp/", "guzzlehttp/", "faraday",
    "ruby/", "php/", "dart/", "reqwest/", "http.rb/", "python/",
)


def _has_version(text):
    """True when some token in the string looks like name/1.2. Pure helper."""
    for part in str(text).split():
        if "/" in part:
            tail = part.split("/", 1)[1].lstrip("vV")
            if tail[:1].isdigit():
                return True
    return False


def grade_user_agent(value):
    """Grade a User-Agent string. Pure.

    Five grades, because "absent" and "present but useless" are different
    findings with different urgencies: one is why the request is being refused
    right now, the other is why nobody will be able to reach you later.
    """
    if value is None:
        return ("absent",
                "no User-Agent header at all. GitHub refuses the request "
                "before it considers the credential, so this fails on "
                "endpoints that need no credential.")
    text = str(value).strip()
    if not text:
        return ("empty",
                "the header is present with an empty value, which is refused "
                "exactly as if it had never been set.")
    low = text.lower()
    for prefix in LIBRARY_DEFAULTS:
        if low.startswith(prefix):
            return ("library-default",
                    "the header names the HTTP library rather than your "
                    "integration. The request works; nobody at GitHub can "
                    "tell your traffic from anyone else's using that library.")
    has_version = _has_version(text)
    has_contact = "http" in low or "@" in text
    if has_version and has_contact:
        return ("descriptive",
                "names the application, a version and a way to reach you. "
                "Nothing to change.")
    if has_version or has_contact:
        return ("named",
                "identifies the caller, but only halfway. Add whichever half "
                "is missing: a version, or a URL or address to reach you at.")
    return ("opaque",
            "present and custom, but it names nothing anyone could act on. "
            "Add a version and a contact.")


def classify_403(message, headers):
    """Sort a 403 into the four things it means on this API. Pure.

    Order matters. The User-Agent rule names itself, so it is checked first and
    never inferred. A secondary limit says so in the body. Primary exhaustion
    says so in a header rather than in words. Everything else is authorization,
    and none of it is repaired by a header.
    """
    text = (message or "").lower()
    head = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    if "user-agent" in text or "administrative rules" in text:
        return ("user-agent-rule",
                "the body names the rule: GitHub requires a User-Agent header "
                "on every API request and refuses the ones that arrive "
                "without it.")
    if "secondary rate limit" in text or "abuse detection" in text:
        return ("secondary-rate-limit",
                "a secondary limit, which is about the shape of the traffic "
                "rather than the number of requests. Slow down and honour "
                "retry-after; no header changes this.")
    if head.get("x-ratelimit-remaining") == "0":
        return ("primary-rate-limit",
                "x-ratelimit-remaining is zero, so this is the hourly quota "
                "and the reset time is on the same response.")
    if "saml" in text or "single sign-on" in text or "sso" in text:
        return ("sso-enforcement",
                "an organization enforcing SSO is hiding the resource from a "
                "credential that has not been authorized for it.")
    if ("not accessible by integration" in text or "must have admin" in text
            or "resource not accessible" in text or "permission" in text):
        return ("permission",
                "an authorization refusal: the credential reached GitHub, was "
                "accepted, and is not allowed to do this.")
    if "ip address" in text or "allow list" in text or "allowlist" in text:
        return ("ip-allow-list",
                "an organization IP allow list refused the source address. "
                "The repair is a network conversation, not a code change.")
    return ("unclassified-403",
            "the body does not match any of the shapes this script knows. "
            "Read it literally; it is the most specific thing you have.")


def verdict(status, message, headers, user_agent_sent):
    """Combine a status, a body message and what the client actually sent. Pure.

    A successful request is still worth a finding. The rule is satisfied by any
    non-empty string, so a 200 proves nothing about whether the header names
    anything, and that is the state that survives for years unnoticed.
    """
    grade, detail = grade_user_agent(user_agent_sent)
    if status == 403:
        cause, why = classify_403(message, headers)
        if cause == "user-agent-rule":
            return ("user-agent-missing",
                    "%s What the client actually sent: %s."
                    % (why, "nothing"
                       if grade in ("absent", "empty") else repr(user_agent_sent)))
        return (cause, "%s This is a 403, but not the one this page is about, "
                       "and no User-Agent will repair it." % why)
    if status == 401:
        return ("not-a-user-agent-problem",
                "a 401 means a credential was received and refused, or was "
                "required and never arrived. The User-Agent rule answers 403 "
                "and never 401.")
    if status >= 400:
        return ("other-failure",
                "status %d, which the User-Agent rule does not produce. The "
                "header that was sent grades as %s." % (status, grade))
    if grade in ("descriptive", "named"):
        return ("user-agent-ok",
                "the request succeeded and the header identifies the caller. "
                "%s" % detail)
    return ("identifiable-agent-missing",
            "the request succeeded, so the rule itself is satisfied, but %s"
            % detail)


def suggest_user_agent(app, version="1.0", contact=None):
    """Build the replacement header value. Pure.

    Deliberately boring: a slug, a version, and a contact in the parenthesised
    form GitHub's own examples use. The value of this function is that it
    always produces something that grades as descriptive.
    """
    slug = "".join(c if c.isalnum() else "-" for c in str(app).lower())
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = slug.strip("-") or "unnamed-integration"
    agent = "%s/%s" % (slug, version)
    if contact:
        agent += " (+%s)" % contact
    return agent


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default="/",
                    help="the API path that was refused. The default is the "
                         "REST root, which any anonymous caller may read")
    ap.add_argument("--app", default="",
                    help="the name of your integration, for the suggested header")
    ap.add_argument("--contact", default="",
                    help="a URL or address GitHub could use to reach you")
    ap.add_argument("--no-user-agent", action="store_true",
                    help="reproduce the refusal by removing the header entirely")
    args = ap.parse_args()

    session = requests.Session()
    session.headers.update({
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    if args.no_user_agent:
        # requests drops a header set to None rather than falling back to its
        # own default, which is the only way to actually reproduce this from a
        # client that is trying to be helpful.
        session.headers["User-Agent"] = None
    elif args.app:
        session.headers["User-Agent"] = suggest_user_agent(
            args.app, contact=args.contact or None)

    token = os.environ.get("GITHUB_TOKEN")
    if token:
        session.headers["Authorization"] = "Bearer " + token
    else:
        log.info("no GITHUB_TOKEN set, which is fine: the User-Agent rule is "
                 "applied before authentication, so an anonymous request "
                 "demonstrates it exactly as well")

    url = API + args.path if args.path.startswith("/") else args.path
    response = session.get(url, timeout=30)
    try:
        body = response.json()
    except ValueError:
        body = None
    message = body.get("message") if isinstance(body, dict) else None
    headers = {k.lower(): v for k, v in response.headers.items()}

    # The authoritative reading of what went on the wire, after redirects and
    # after whatever the library added. It lives on the request object the
    # client already holds, so it needs no second request and no control.
    sent = response.request.headers.get("User-Agent")

    log.info("%s returned %d", args.path, response.status_code)
    log.info("user-agent sent: %s", sent if sent else "none")
    log.info("body message:    %s", message or "none")
    log.info("remaining quota: %s", headers.get("x-ratelimit-remaining", "not reported"))

    state, detail = verdict(response.status_code, message, headers, sent)
    log.info("%s: %s", state, detail)

    if state in ("user-agent-missing", "identifiable-agent-missing"):
        want = suggest_user_agent(args.app or "your integration",
                                  contact=args.contact or "https://example.com/contact")
        log.info("repair: set this once on the session, client or transport, "
                 "never per request: User-Agent: %s", want)
        log.info("repair: a request that forgets the header should be "
                 "impossible to construct, not merely rare.")

    print(json.dumps({"path": args.path, "status": response.status_code,
                      "user_agent_sent": sent, "message": message,
                      "state": state}, indent=2))
    return 1 if state in ("user-agent-missing", "identifiable-agent-missing") else 0


if __name__ == "__main__":
    sys.exit(main())
