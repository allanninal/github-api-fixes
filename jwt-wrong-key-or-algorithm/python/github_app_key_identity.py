"""Say which GitHub App a private key belongs to, without printing the key.

Read only. One request, GET /app, sent with a JWT you already hold. Nothing is
minted, rotated, registered or changed, and the script never signs anything
itself: it inspects the key file and asks GitHub who answered.

The output contains a PEM label, a line count, a byte count and a truncated
SHA-256 fingerprint. It never contains the key, any part of the key, or the
JWT. A fingerprint is the right thing to compare across machines precisely
because it can be pasted into a chat window without consequence.

The blind spot is stated rather than worked around: GitHub does not publish the
public keys registered on an App, so nothing here can prove a key is registered
except by using it. A 200 from GET /app is that proof. A 401 narrows the cause
to a short list without choosing between its entries.
"""
import argparse
import base64
import hashlib
import json
import logging
import os
import re
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_app_key_identity")

API = "https://api.github.com"
UA = "github-app-key-identity/1.0"

# The two characters backslash and n, which is what an environment variable
# holds when a PEM's newlines were escaped rather than embedded. This is the
# single most common way a working key stops being a key.
ESCAPED_NEWLINE = "\\n"

# A 2048-bit RSA private key is around 1200 bytes of DER. Anything much under
# this is a different kind of key or a truncated one.
MIN_RSA_DER = 500

# The labels GitHub's own key downloads carry, and the one PKCS#8 alternative
# that every library still accepts.
USABLE_LABELS = {"RSA PRIVATE KEY": "pkcs1-rsa-key", "PRIVATE KEY": "pkcs8-key"}

REPAIRS = {
    "no-key-present":
        "set GITHUB_APP_PRIVATE_KEY to the PEM downloaded from the App's "
        "settings page. Nothing can be said about a key that is not there.",
    "escaped-newlines":
        "the value contains the two characters backslash and n where line "
        "breaks belong, so some layer between the settings page and this "
        "process escaped them. Base64-encode the whole PEM for transport and "
        "decode it in the process; then no layer in between has an opinion.",
    "single-line-pem":
        "the PEM has lost its line breaks entirely. Same repair: carry it "
        "base64-encoded rather than raw.",
    "not-a-pem":
        "there is no BEGIN line, so this is not a PEM at all. Check what the "
        "secret store actually returned.",
    "truncated-pem":
        "there is a BEGIN line and no matching END line, so the value was cut "
        "short. Secret stores with a length limit do this quietly.",
    "encrypted-key":
        "this key is passphrase-protected. GitHub does not issue encrypted "
        "keys, so this one was re-encrypted locally; decrypt it or download a "
        "fresh key from the App.",
    "openssh-format":
        "this is an OpenSSH key, which is what ssh-keygen produces. It is not "
        "the key GitHub issued for the App. Download the App's private key "
        "from its settings page.",
    "public-key-not-private":
        "this is the public half of a pair. The public key cannot sign, so no "
        "JWT made with it will ever verify.",
    "certificate-not-key":
        "this is a certificate rather than a key. Something is reading the "
        "wrong entry out of the secret store.",
    "not-an-rsa-key":
        "GitHub App JWTs must be signed RS256, which needs an RSA key. This "
        "key uses a different algorithm family and cannot sign one.",
    "unknown-pem-label":
        "the PEM label is not one this check recognises, which usually means "
        "the wrong file entirely.",
    "body-not-base64":
        "the body between the BEGIN and END lines is not valid base64, so the "
        "PEM was corrupted in transit or edited by hand.",
    "too-small-for-rsa":
        "the decoded body is too small to be an RSA private key of any usable "
        "size, so this is either truncated or a different kind of key.",
    "pkcs1-rsa-key":
        "this is the PKCS#1 RSA private key GitHub issues.",
    "pkcs8-key":
        "this is a PKCS#8 wrapper, which every sensible JWT library accepts.",
}


def unwrap(text):
    """Undo base64 transport if the value is a wrapped PEM. Pure.

    Carrying a PEM base64-encoded is the recommended way to survive an
    environment variable, so a value with no BEGIN line that decodes to one is
    not an error. Returns the PEM and whether it was wrapped.
    """
    raw = str(text or "").strip()
    if not raw or "BEGIN" in raw:
        return raw, False
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
    except Exception:
        return raw, False
    if "BEGIN" in decoded:
        return decoded, True
    return raw, False


def inspect_pem(text):
    """Reduce a PEM to a label, a shape and a fingerprint. Pure.

    Never returns any part of the key. The fingerprint is the SHA-256 of the
    decoded DER body, truncated, which identifies the file without revealing
    it and is therefore safe to compare in a chat window.
    """
    raw = str(text or "")
    out = {"state": None, "label": None, "fingerprint": None,
           "der_bytes": None, "lines": 0}
    if not raw.strip():
        out["state"] = "no-key-present"
        return out
    out["lines"] = len(raw.strip().splitlines())
    if ESCAPED_NEWLINE in raw:
        out["state"] = "escaped-newlines"
        return out

    found = re.search(r"-----BEGIN ([A-Z0-9 ]+)-----", raw)
    if not found:
        out["state"] = "not-a-pem"
        return out
    label = found.group(1).strip()
    out["label"] = label

    if label == "ENCRYPTED PRIVATE KEY" or "Proc-Type: 4,ENCRYPTED" in raw:
        out["state"] = "encrypted-key"
        return out
    if label == "OPENSSH PRIVATE KEY":
        out["state"] = "openssh-format"
        return out
    if label.endswith("PUBLIC KEY"):
        out["state"] = "public-key-not-private"
        return out
    if label == "CERTIFICATE":
        out["state"] = "certificate-not-key"
        return out
    if label in ("EC PRIVATE KEY", "DSA PRIVATE KEY"):
        out["state"] = "not-an-rsa-key"
        return out
    if label not in USABLE_LABELS:
        out["state"] = "unknown-pem-label"
        return out
    if ("-----END " + label + "-----") not in raw:
        out["state"] = "truncated-pem"
        return out
    if out["lines"] < 3:
        out["state"] = "single-line-pem"
        return out

    body = "".join(line.strip() for line in raw.splitlines()
                   if line.strip() and not line.strip().startswith("-----"))
    try:
        der = base64.b64decode(body, validate=True)
    except Exception:
        out["state"] = "body-not-base64"
        return out
    out["der_bytes"] = len(der)
    out["fingerprint"] = hashlib.sha256(der).hexdigest()[:16]
    if len(der) < MIN_RSA_DER:
        out["state"] = "too-small-for-rsa"
        return out
    out["state"] = USABLE_LABELS[label]
    return out


def usable(state):
    """Whether a key in this state could sign an RS256 JWT at all. Pure."""
    return state in ("pkcs1-rsa-key", "pkcs8-key")


def repair_for(state):
    """The one sentence worth printing under a PEM state. Pure."""
    return REPAIRS.get(state, "this state has no stock repair; read the label.")


def issuer_form(value):
    """Classify what was put in the iss claim. Pure.

    iss must be the App's client ID or its numeric App ID. A slug, an owner
    name or an installation ID all produce Integration not found, which is a
    different failure from a key that does not verify.
    """
    text = str(value or "").strip()
    if not text:
        return "no-issuer"
    if text.isdigit():
        return "app-id"
    if text.startswith("Iv1.") or text.startswith("Iv23"):
        return "client-id"
    return "unusable-issuer"


def interpret(status, message):
    """Map a GET /app response to the defect it names. Pure.

    GitHub deliberately will not say which part of verification failed, so the
    decode message covers five causes at once and this function says so rather
    than picking one. The claim messages are named only to hand them off.
    """
    if status == 200:
        return ("key-accepted",
                "the JWT verified against a key registered on this App.")
    text = str(message or "").lower()
    if "could not be decoded" in text:
        return ("signature-rejected",
                "GitHub could not verify the JWT. That one message covers a "
                "key from another App, a key deleted during rotation, an "
                "algorithm other than RS256, and a PEM whose newlines were "
                "destroyed. Compare the fingerprint against a machine that "
                "works to split the list.")
    if "integration not found" in text:
        return ("issuer-does-not-resolve",
                "iss does not name an App GitHub can find, so the claim is "
                "wrong rather than the key. It must be the client ID or the "
                "numeric App ID.")
    if "issued at" in text or "'iat'" in text:
        return ("clock-problem-not-key",
                "GitHub is complaining about iat, which is clock drift on the "
                "signing host and a different repair entirely.")
    if "too far in the future" in text:
        return ("lifetime-problem-not-key",
                "GitHub is complaining about exp, so the requested lifetime is "
                "over the ceiling and the key is fine.")
    if "bad credentials" in text:
        return ("not-a-jwt",
                "GitHub parsed the credential and refused it outright, which "
                "is what happens when an installation access token is sent to "
                "a route that wants the App JWT.")
    return ("unrelated",
            "the response does not name a key or a claim, so this failure has "
            "another cause.")


def reconcile(app, expected):
    """Say whether GET /app answered as the App you meant. Pure.

    The failure this catches makes no noise at all: a staging key against a
    staging App works perfectly, on the wrong account, with the wrong
    installations, and returns 200 the whole time.
    """
    if not isinstance(app, dict):
        return ("no-app-body",
                "GET /app returned nothing that could be read as an App.")
    label = "%s (id %s, client_id %s)" % (app.get("slug") or app.get("name"),
                                          app.get("id"), app.get("client_id"))
    known = {str(app.get(field) or "").lower()
             for field in ("id", "client_id", "slug", "name")}
    known.discard("")
    want = str(expected or "").strip().lower()
    if not want:
        return ("no-expectation-given",
                "GET /app answered as %s. Pass --expect to have that checked "
                "rather than reported." % label)
    if want in known:
        return ("identity-matches", "GET /app answered as %s." % label)
    return ("authenticated-as-another-app",
            "you expected %s and the key authenticated as %s. The credential "
            "works; it belongs to a different App, which is how a staging key "
            "reaches production without anything failing." % (expected, label))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--expect", default=None,
                    help="the App you believe this key belongs to, as a slug, "
                         "a name, a numeric id or a client id")
    ap.add_argument("--iss", default=None,
                    help="the value your signing code puts in the iss claim, "
                         "checked for shape only")
    ap.add_argument("--offline", action="store_true",
                    help="inspect the key and skip the confirming GET /app")
    args = ap.parse_args()

    pem, wrapped = unwrap(os.environ.get("GITHUB_APP_PRIVATE_KEY"))
    if wrapped:
        log.info("the key was carried base64-encoded, which is the shape that "
                 "survives an environment variable")
    key = inspect_pem(pem)
    log.info("key: label=%s fingerprint=%s der=%sB lines=%d",
             key["label"] or "none", key["fingerprint"] or "none",
             key["der_bytes"] if key["der_bytes"] is not None else "?",
             key["lines"])
    log.info("%s: %s", key["state"], repair_for(key["state"]))

    if args.iss is not None:
        form = issuer_form(args.iss)
        log.info("iss form: %s", form)
        if form == "unusable-issuer":
            log.info("repair: iss must be the App's client ID or its numeric "
                     "App ID. Anything else returns Integration not found.")

    live_state = None
    identity_state = None
    if not args.offline:
        jwt = os.environ.get("GITHUB_APP_JWT")
        if not jwt:
            log.warning("set GITHUB_APP_JWT to the JWT your signing code "
                        "produces, or pass --offline to inspect the key only")
        else:
            # The JWT is sent and nothing else. It is not decoded, stored or
            # logged, in whole or in part.
            r = requests.get(API + "/app", timeout=30, headers={
                "Authorization": "Bearer " + jwt,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": UA,
            })
            try:
                body = r.json()
            except ValueError:
                body = None
            message = body.get("message") if isinstance(body, dict) else None
            log.info("GET /app returned %d", r.status_code)
            live_state, live_detail = interpret(r.status_code, message)
            log.info("%s: %s", live_state, live_detail)
            if r.status_code == 200:
                identity_state, identity_detail = reconcile(body, args.expect)
                log.info("%s: %s", identity_state, identity_detail)

    print(json.dumps({"label": key["label"], "fingerprint": key["fingerprint"],
                      "der_bytes": key["der_bytes"], "lines": key["lines"],
                      "key_state": key["state"], "live_state": live_state,
                      "identity_state": identity_state}, indent=2))
    ok = usable(key["state"]) and live_state in (None, "key-accepted") \
        and identity_state != "authenticated-as-another-app"
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
