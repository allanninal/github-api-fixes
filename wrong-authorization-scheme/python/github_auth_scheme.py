"""Check the Authorization scheme word against the shape of the credential.

Read only. The diagnosis is computed on this machine from the credential's own
first characters; the only network work is two GETs to the same path that
differ in one word, which is the experiment that isolates the variable.

Bearer works for every GitHub credential. The older token word works for
personal access tokens, OAuth user tokens and installation access tokens, and
not for a GitHub App JWT. That one pairing fails with the generic bad
credentials message, which names the credential and hides the envelope.

The credential value is never printed, logged or returned. Only its type is.
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_auth_scheme")

API = "https://api.github.com"
UA = "github-auth-scheme/1.0 (+https://example.com/contact)"

# GitHub's prefixed credential formats. Longest first so github_pat_ is not
# shadowed by a shorter neighbour if this table ever grows one.
PREFIXES = (
    ("github_pat_", "fine-grained-pat"),
    ("ghp_", "classic-pat"),
    ("gho_", "oauth-user-token"),
    ("ghu_", "user-to-server-token"),
    ("ghs_", "installation-token"),
    ("ghr_", "refresh-token"),
)

# The base64url alphabet, plus padding, which is what a JWT's three segments are
# drawn from. Used for recognition only; nothing here decodes anything.
B64URL = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=")

# What each credential type accepts in front of it, lowercased. Bearer is
# correct for all of them; the legacy word is accepted for everything except an
# App JWT, which is the pairing this note exists for.
ACCEPTS = {
    "app-jwt": ("bearer",),
    "classic-pat": ("bearer", "token"),
    "fine-grained-pat": ("bearer", "token"),
    "oauth-user-token": ("bearer", "token"),
    "user-to-server-token": ("bearer", "token"),
    "installation-token": ("bearer", "token"),
    "legacy-pat": ("bearer", "token"),
    "refresh-token": (),
    "unknown": ("bearer", "token"),
    "absent": (),
}

# The short set of sentences GitHub uses for an authentication failure. The
# value of this table is that two of them are much more specific than the third.
MESSAGES = (
    ("a json web token could not be decoded",
     ("jwt-expected",
      "the endpoint wanted an App JWT and got something that is not one. That "
      "is a credential type mismatch rather than a scheme one, and it is the "
      "helpful failure: it names its subject.")),
    ("requires authentication",
     ("nothing-arrived",
      "no Authorization header reached GitHub at all, so the scheme word is "
      "not the question yet. Something between your process and GitHub "
      "dropped the header.")),
    ("bad credentials",
     ("received-and-refused",
      "GitHub parsed something and did not accept it. A JWT under the token "
      "word produces exactly this, and so does a dead token, so the message "
      "alone does not separate them.")),
)


def looks_like_jwt(value):
    """Recognise a JWT by shape alone. Pure.

    Three dot separated base64url segments, the first of which starts with the
    encoding of an opening brace and a quote. The payload is deliberately not
    decoded: what the claims say is a different question and a different note.
    """
    if not value:
        return False
    parts = str(value).split(".")
    if len(parts) != 3 or not all(parts):
        return False
    if not all(set(p) <= B64URL for p in parts):
        return False
    return parts[0].startswith("eyJ")


def credential_kind(value):
    """Name a credential's type from its own text. Pure.

    Returns a type name and never the value, so this can be called on the real
    secret and its result can go straight into a log line.
    """
    if value is None or not str(value).strip():
        return "absent"
    text = str(value).strip()
    if looks_like_jwt(text):
        return "app-jwt"
    for prefix, kind in PREFIXES:
        if text.startswith(prefix):
            return kind
    if len(text) == 40 and all(c in "0123456789abcdef" for c in text.lower()):
        return "legacy-pat"
    return "unknown"


def parse_authorization(header):
    """Split an Authorization header into a scheme and whether a value follows.

    Pure. The value is never returned: nothing downstream has a reason to hold
    it, and every structure that does is another copy of the secret.
    """
    if header is None:
        return {"scheme": None, "has_credential": False, "words": 0}
    words = str(header).split()
    if not words:
        return {"scheme": None, "has_credential": False, "words": 0}
    if len(words) == 1:
        return {"scheme": None, "has_credential": True, "words": 1}
    return {"scheme": words[0], "has_credential": True, "words": len(words)}


def check_pairing(scheme, kind):
    """Decide whether a scheme word and a credential type belong together. Pure.

    Returns (state, detail, repair). Five of the seven states are decided
    without a request, which is the point: this is a startup assertion, not an
    incident tool.
    """
    word = (scheme or "").lower()
    if kind == "absent":
        return ("no-credential",
                "there is no credential to pair a scheme with. The variable "
                "holding it is empty or unset.",
                "set the credential in the environment and read it from there")
    if word == "basic":
        return ("basic-scheme",
                "Basic is a retired mechanism for this API. It fails for a "
                "reason that has nothing to do with which credential you hold.",
                "send Authorization: Bearer with the token instead of Basic")
    if scheme is None:
        return ("scheme-missing",
                "the header carries a bare value with no word in front of it. "
                "GitHub cannot tell what it is being offered and refuses it "
                "with the same message a dead token gets.",
                "prefix the value with Bearer and a single space")
    if word not in ("bearer", "token"):
        return ("unknown-scheme",
                "%s is not a scheme this API reads. Only Bearer and the legacy "
                "token word are accepted." % scheme,
                "replace the scheme word with Bearer")
    if kind == "refresh-token":
        return ("refresh-token-sent",
                "a refresh token is not an API credential under any scheme. It "
                "is exchanged for a user token, and that result is what goes "
                "on the wire.",
                "exchange the refresh token first, then send what comes back")
    if kind == "app-jwt" and word == "token":
        return ("jwt-with-token-scheme",
                "an App JWT is only read under Bearer. Under the token word it "
                "is refused with the generic bad credentials message, which "
                "names the credential and hides the envelope.",
                "change the word token to Bearer and send the same JWT")
    if word == "token":
        return ("legacy-scheme-accepted",
                "the token word still works for this credential type, so "
                "nothing is failing because of it today. It is the older "
                "spelling, and it is the one that breaks when the same code "
                "path later carries a JWT.",
                "move this helper to Bearer for every credential type")
    return ("bearer-ok",
            "Bearer is correct for this credential type, so the envelope is "
            "not the problem. If the call still fails, the credential itself "
            "is the subject.",
            "none")


def explain_401(message):
    """Map GitHub's short set of authentication messages onto causes. Pure."""
    text = (message or "").strip().lower().rstrip(".")
    for needle, result in MESSAGES:
        if needle in text:
            return result
    return ("unmapped-message",
            "not one of the sentences GitHub uses for an authentication "
            "failure, so read it literally rather than through this table.")


def get(path, scheme, token):
    """One GET under one scheme word. Returns (status, message)."""
    url = API + path if path.startswith("/") else path
    response = requests.get(url, timeout=30, headers={
        "Authorization": "%s %s" % (scheme, token),
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })
    try:
        body = response.json()
    except ValueError:
        body = None
    return response.status_code, (body.get("message")
                                  if isinstance(body, dict) else None)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default="/user",
                    help="the API path that was refused")
    ap.add_argument("--scheme", default="token",
                    help="the scheme word your client currently sends")
    ap.add_argument("--offline", action="store_true",
                    help="do the pairing check and send nothing at all")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    kind = credential_kind(token)
    if kind == "absent":
        log.error("set GITHUB_TOKEN. There is no credential to pair a scheme "
                  "with, which is its own answer but not this one")
        return 2

    header = parse_authorization("%s %s" % (args.scheme, token))
    state, detail, repair = check_pairing(header["scheme"], kind)

    log.info("credential type: %s", kind)
    log.info("scheme word:     %s", header["scheme"] or "none, a bare value")
    log.info("accepted words:  %s", ", ".join(ACCEPTS.get(kind, ())) or "none")
    log.info("%s: %s", state, detail)

    result = {"path": args.path, "credential_type": kind,
              "scheme": header["scheme"], "state": state}

    if not args.offline:
        configured = get(args.path, args.scheme, token)
        log.info("as configured (%s):  %d %s", args.scheme, configured[0],
                 configured[1] or "")
        if configured[0] == 401:
            cause, why = explain_401(configured[1])
            log.info("  %s: %s", cause, why)
        result["configured"] = {"scheme": args.scheme, "status": configured[0],
                                "message": configured[1]}

        if args.scheme.lower() != "bearer":
            recommended = get(args.path, "Bearer", token)
            log.info("as recommended (Bearer): %d %s", recommended[0],
                     recommended[1] or "")
            result["recommended"] = {"scheme": "Bearer",
                                     "status": recommended[0],
                                     "message": recommended[1]}
            if recommended[0] != configured[0]:
                log.info("the scheme word alone changed the outcome, which is "
                         "as close to proof as this gets")
            elif configured[0] >= 400:
                log.info("both words failed identically, so the envelope is "
                         "innocent. Look at the credential itself, the "
                         "account it belongs to, or the endpoint's own rules")

    if repair != "none":
        log.info("repair: %s", repair)
    print(json.dumps(result, indent=2))
    return 1 if state in ("jwt-with-token-scheme", "scheme-missing",
                          "unknown-scheme", "basic-scheme",
                          "refresh-token-sent") else 0


if __name__ == "__main__":
    sys.exit(main())
