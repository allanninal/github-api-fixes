"""Decide which authentication mechanism a GitHub client is using, offline.

Read only, and one request: GET /user with a Bearer header built from the
environment. The script deliberately never transmits a username and password,
even to reproduce the documented 401. Sending a live password to be told it
will not work costs you a password in a proxy log and buys you nothing the
header did not already say.

Nothing here prints the secret. The report carries the scheme, the length and
whether a username was present.
"""
import argparse
import base64
import binascii
import json
import logging
import os
import re
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_auth_scheme_check")

API = "https://api.github.com"
UA = "github-auth-scheme-check/1.0"

# Prefixes GitHub issues its tokens with. The list is used only to answer "is
# the half after the colon a token or a password", so a new prefix appearing
# here later would make the check more precise rather than change its meaning.
TOKEN_PREFIXES = ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_")
LEGACY_HEX = re.compile(r"^[0-9a-f]{40}$")

# The message that means the mechanism is retired, as opposed to a 401 whose
# message is about the credential itself being wrong.
REMOVED = "support for password authentication was removed"

# Call sites that build the retired header without the word Authorization ever
# appearing. Matched by shape; the matched text is never echoed.
CALL_SITES = (
    ("curl -u", re.compile(r"\bcurl\b[^\n]*?\s(-u|--user)\s")),
    ("Invoke-WebRequest -Credential", re.compile(r"-Credential\b")),
    ("netrc entry", re.compile(r"^\s*machine\s+[\w.]*github", re.I)),
    ("two-string client constructor", re.compile(r"\b(username|user)\s*=\s*[^,\n]+,\s*password\s*=")),
)


def looks_like_token(secret):
    """Is this string shaped like a GitHub token rather than a password? Pure."""
    value = str(secret or "")
    if value.startswith(TOKEN_PREFIXES):
        return True
    return bool(LEGACY_HEX.match(value))


def parse_auth_header(value):
    """Describe an Authorization header without revealing what is in it. Pure.

    Returns the scheme, whether a username was present, the length of the
    secret and whether the secret is token-shaped. The secret itself is never
    part of the return value, so no caller can accidentally log it.
    """
    raw = (value or "").strip()
    if not raw:
        return {"scheme": None, "username_present": False, "secret_length": 0,
                "token_shaped": False, "decoded": True}
    parts = raw.split(None, 1)
    scheme = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if scheme != "basic":
        return {"scheme": scheme, "username_present": False,
                "secret_length": len(rest), "token_shaped": looks_like_token(rest),
                "decoded": True}

    try:
        decoded = base64.b64decode(rest, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return {"scheme": "basic", "username_present": False, "secret_length": 0,
                "token_shaped": False, "decoded": False}

    user, sep, secret = decoded.partition(":")
    return {"scheme": "basic", "username_present": bool(user) or bool(sep),
            "secret_length": len(secret), "token_shaped": looks_like_token(secret),
            "decoded": True}


def classify(parsed):
    """Name the mechanism from a parsed header. Pure."""
    scheme = (parsed or {}).get("scheme")
    if scheme is None:
        return "no-credential"
    if scheme == "basic":
        if not parsed.get("decoded"):
            return "undecodable-basic"
        return "token-basic" if parsed.get("token_shaped") else "password-basic"
    if scheme == "bearer":
        return "bearer"
    if scheme == "token":
        return "token-scheme"
    return "unknown-scheme"


def password_removed(body):
    """Does this response body carry the retired-mechanism message? Pure."""
    if isinstance(body, dict):
        text = str(body.get("message", ""))
    else:
        text = str(body or "")
    return REMOVED in " ".join(text.lower().split())


def replacement_header():
    """The one line that replaces every retired form. Pure."""
    return "Authorization: Bearer $GITHUB_TOKEN"


def scan_snippet(text):
    """Find call sites that build a username-and-password header. Pure.

    Reports the line number and the shape, never the line. A snippet audit that
    quotes the matching line back at you puts the credential in the report.
    """
    findings = []
    for number, line in enumerate(str(text or "").splitlines(), start=1):
        for label, pattern in CALL_SITES:
            if pattern.search(line):
                findings.append({"line": number, "form": label})
    return findings


def verdict(kind, probe_status, probe_body):
    """Turn the classification and the Bearer probe into a finding. Pure."""
    if kind == "password-basic":
        return ("password-basic",
                "the header is a username and a password. That mechanism was "
                "removed from the API and no password will ever be accepted "
                "again. Nothing was sent: the shape is the answer, and "
                "transmitting it would only add a copy of the password to your "
                "proxy log.")
    if kind == "token-basic":
        return ("token-basic",
                "the header is a username and a token. That still works on much "
                "of the API, which is why it survives, but the username is "
                "meaningless and the form is on the way out. Replace it.")
    if kind == "undecodable-basic":
        return ("undecodable-basic",
                "the header says Basic but the payload is not valid base64, so "
                "something is double-encoding or truncating it before it goes "
                "out. GitHub will read this as no credential at all.")
    if kind == "no-credential":
        return ("no-credential",
                "no Authorization header was configured, so requests go out "
                "anonymous rather than refused, and quietly get the 60 an hour "
                "tier instead of an error.")
    if kind == "unknown-scheme":
        return ("unknown-scheme",
                "the scheme is neither Basic, Bearer nor token, so GitHub will "
                "ignore it and treat the request as unauthenticated.")

    if probe_status == 200:
        return ("ok", "the documented scheme, and the credential behind it "
                      "authenticates.")
    if password_removed(probe_body):
        return ("password-removed-message",
                "the scheme looks right but GitHub still answered with the "
                "retired-mechanism message, so something downstream is "
                "rewriting the header into Basic before it leaves.")
    if probe_status == 401:
        return ("credential-rejected",
                "the mechanism is correct and the credential is not. That is a "
                "different problem from this one: the token is wrong, revoked "
                "or expired rather than badly wrapped.")
    return ("probe-inconclusive",
            "the scheme is correct; the probe returned %s rather than 200, so "
            "judge the credential separately." % probe_status)


def get(session, path):
    """One GET. Returns (status, json-or-None)."""
    url = API + path if path.startswith("/") else path
    r = session.get(url, timeout=30)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snippet-file",
                    help="optional path to a script or config to sweep for "
                         "call sites that build the retired header")
    args = ap.parse_args()

    configured = os.environ.get("GITHUB_AUTH_HEADER")
    parsed = parse_auth_header(configured)
    kind = classify(parsed)
    log.info("scheme: %s, secret %d char(s), %s",
             kind, parsed["secret_length"],
             "username present" if parsed["username_present"] else "no username")

    if kind == "password-basic":
        log.warning("not sending this header. A password is refused by every "
                    "endpoint, and posting it would put a live password in one "
                    "more log")

    probe_status, probe_body = None, None
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        session = requests.Session()
        session.headers.update({
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": UA,
        })
        probe_status, probe_body = get(session, "/user")
        who = (probe_body or {}).get("login", "an unnamed user") \
            if isinstance(probe_body, dict) else "an unnamed user"
        log.info("probe: GET /user returned %s%s", probe_status,
                 " as " + who if probe_status == 200 else "")
    else:
        log.info("set GITHUB_TOKEN to also prove the credential itself is good")

    state, detail = verdict(kind, probe_status, probe_body)
    log.info("%s: %s", state, detail)

    if state in ("password-basic", "token-basic", "undecodable-basic",
                 "no-credential", "unknown-scheme"):
        log.info("repair: send exactly this and delete the username field: %s",
                 replacement_header())
        log.info("repair: Authorization: token TOKEN is still accepted if a "
                 "library will not emit Bearer, but Basic is not worth keeping.")

    sites = []
    if args.snippet_file:
        with open(args.snippet_file, "r", encoding="utf-8", errors="replace") as fh:
            sites = scan_snippet(fh.read())
        for site in sites:
            log.warning("line %d builds the retired header via %s",
                        site["line"], site["form"])
        if not sites:
            log.info("no call sites in %s build a username and password header",
                     args.snippet_file)

    print(json.dumps({"scheme": kind, "username_present": parsed["username_present"],
                      "secret_length": parsed["secret_length"],
                      "probe_status": probe_status, "call_sites": sites,
                      "state": state}, indent=2))
    return 0 if state == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
