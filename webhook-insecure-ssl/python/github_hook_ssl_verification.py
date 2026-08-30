"""Find webhooks GitHub delivers to without checking the TLS certificate.

Read only. Every call is a GET. Changing the flag is a write and this script
does not do it: it prints the request, as a full config rather than a single
field, because a webhook's config is replaced rather than merged and the secret
you read back is a mask.

config.insecure_ssl set to "1" tells GitHub to skip verification of the
endpoint's certificate. The connection is still TLS, so this is not the same as
posting in the clear; what is lost is the guarantee that the endpoint is yours.
Anything that can be answered instead of you receives correctly signed payloads
and can replay them afterwards.

Deliveries succeed the whole time, which is why nothing else reports this.

The secret is never printed. Its presence is read only to decide whether the
repair needs to mention rotation.

Environment:

    GITHUB_TOKEN    a read-only token that can see the repository's hooks
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_hook_ssl_verification")

API = "https://api.github.com"
UA = "github-hook-ssl-verification/1.0"

# The two-value string, in every spelling it arrives in. Both "0" and "1" are
# non-empty strings, so a truthy test on this field reports every correctly
# configured hook as insecure. Parse it; do not test it.
INSECURE_ON = ("1", "true", "yes", "on")
INSECURE_OFF = ("0", "false", "no", "off")


def config_of(hook):
    """The config object of a hook, or an empty dict. Pure."""
    if not isinstance(hook, dict):
        return {}
    config = hook.get("config")
    return config if isinstance(config, dict) else {}


def insecure_flag(hook):
    """Three-state read of insecure_ssl: on, off or unknown. Pure.

    on  means GitHub does not check the certificate
    off means it does
    unknown means the field was absent or unreadable, which is reported rather
            than rounded to either answer
    """
    config = config_of(hook)
    if "insecure_ssl" not in config:
        return "unknown"
    raw = config["insecure_ssl"]
    if isinstance(raw, bool):
        return "on" if raw else "off"
    if isinstance(raw, (int, float)):
        return "on" if raw else "off"
    text = str(raw).strip().lower()
    if text in INSECURE_ON:
        return "on"
    if text in INSECURE_OFF:
        return "off"
    return "unknown"


def scheme_of(hook):
    """The URL scheme of a hook, lower-cased, or "" when there is none. Pure."""
    url = str(config_of(hook).get("url") or "").strip()
    if "://" not in url:
        return ""
    return url.split("://", 1)[0].lower()


def endpoint(hook):
    """The hook's URL with any query string dropped. Pure.

    A URL is printable, a query string is not reliably so: hooks created by
    hand sometimes carry a token in one, and this script prints its findings.
    """
    url = str(config_of(hook).get("url") or "").strip()
    return url.split("?", 1)[0] if url else "an unset URL"


def has_secret(hook):
    """Whether the hook has a secret set. Pure. The value is never read."""
    return "secret" in config_of(hook)


def parsed_time(text):
    """An ISO 8601 timestamp as an aware datetime, or None. Pure."""
    raw = str(text or "").strip()
    if not raw or raw.lower() in ("null", "none"):
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def unchanged_days(hook, now):
    """Days since the hook config was last edited, or None. Pure.

    A lower bound on how long verification has been off, never a start date:
    updated_at moves on any change to the hook, so it says "not since then"
    rather than "since then".
    """
    if not isinstance(hook, dict) or now is None:
        return None
    when = parsed_time(hook.get("updated_at"))
    if when is None:
        return None
    return (now - when).days


def classify(hook, now=None):
    """Sort one hook into a state and a sentence. Pure.

    A hook with no TLS at all is handed to the plaintext question rather than
    counted here. insecure_ssl describes a handshake that hook never performs,
    and folding the two together lets the real finding hide inside a number.
    """
    ident = "hook %s" % (hook.get("id", "?") if isinstance(hook, dict) else "?")
    scheme = scheme_of(hook)
    flag = insecure_flag(hook)
    if not scheme:
        return ("no-url",
                "%s has no usable URL in its config, so there is nothing to "
                "verify a certificate against." % ident)
    if scheme != "https":
        return ("not-applicable",
                "%s posts to a %s:// URL, so no certificate is checked because "
                "no TLS handshake happens. insecure_ssl is not the finding "
                "here; the scheme is." % (ident, scheme))
    if flag == "on":
        age = unchanged_days(hook, now)
        return ("verification-off",
                "%s posts to %s with certificate verification disabled%s. "
                "Deliveries succeed, so nothing else reports this."
                % (ident, endpoint(hook),
                   ", and has not been edited for at least %d day(s)" % age
                   if age is not None else ""))
    if flag == "unknown":
        return ("flag-unreadable",
                "%s does not report a readable insecure_ssl value. Read it in "
                "the hook's settings rather than assuming either answer."
                % ident)
    return ("verified",
            "%s posts to %s and GitHub checks the certificate."
            % (ident, endpoint(hook)))


def repair(state, hook):
    """The change to make, printed as a whole config. Pure.

    Never a single-field update. A webhook's config is replaced rather than
    merged, and config.secret comes back masked, so a read-modify-write of one
    field writes the mask back as the secret or drops it.
    """
    if state == "verification-off":
        rotate = (" and a new secret" if has_secret(hook)
                  else " and a secret, since this hook has none")
        return ("install a certificate that chains to a public root, confirm "
                "it from outside your network, then send the hook's full "
                "config with insecure_ssl \"0\"%s. The config is replaced, not "
                "merged, and the secret you read back is a mask." % rotate)
    if state == "not-applicable":
        return ("move the receiver behind HTTPS and change the URL. Until "
                "then insecure_ssl is a field about a handshake this hook "
                "never performs.")
    if state == "flag-unreadable":
        return ("open the hook's settings and read the SSL verification "
                "setting by hand.")
    if state == "no-url":
        return ("set a URL on this hook, or delete it. A hook with no endpoint "
                "delivers nothing and hides in every audit that counts hooks.")
    return "nothing. GitHub verifies this endpoint's certificate."


def summarize(hooks, now=None):
    """Counts across every hook read. Pure."""
    rows = [h for h in (hooks or []) if isinstance(h, dict)]
    states = [classify(h, now)[0] for h in rows]
    return {"total": len(rows),
            "verification_off": states.count("verification-off"),
            "verified": states.count("verified"),
            "plaintext": states.count("not-applicable"),
            "unreadable": states.count("flag-unreadable") + states.count("no-url")}


def get(session, path):
    """One GET. Returns (status, json-or-None)."""
    url = API + path if path.startswith("/") else path
    r = session.get(url, timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body


def list_hooks(session, scope):
    """Hooks for a repo (owner/name) or an org (@org). Read only."""
    path = ("/orgs/%s/hooks?per_page=100" % scope[1:] if scope.startswith("@")
            else "/repos/%s/hooks?per_page=100" % scope)
    status, body = get(session, path)
    if status != 200 or not isinstance(body, list):
        log.error("GET %s returned %d", path, status)
        return []
    return body


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", action="append", default=[],
                    help="owner/name; repeatable")
    ap.add_argument("--org", action="append", default=[],
                    help="organization login; repeatable")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN to a read-only token that can see the "
                  "repository's hooks")
        return 2
    scopes = list(args.repo) + ["@" + o for o in args.org]
    if not scopes:
        log.error("pass at least one --repo owner/name or --org login")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    now = datetime.now(timezone.utc)
    findings = []
    for scope in scopes:
        label = scope[1:] if scope.startswith("@") else scope
        hooks = list_hooks(session, scope)
        stats = summarize(hooks, now)
        log.info("%d hook(s) on %s", stats["total"], label)
        for hook in hooks:
            state, detail = classify(hook, now)
            findings.append({"scope": label, "hook_id": hook.get("id"),
                             "state": state, "detail": detail,
                             "url": endpoint(hook),
                             "secret_set": has_secret(hook)})
            if state != "verified":
                log.info("%s: %s", state, detail)
                log.info("repair: %s", repair(state, hook))
        if stats["verification_off"] == 0:
            log.info("verified: no hook on %s has certificate verification "
                     "disabled", label)

    print(json.dumps({"scopes": scopes, "findings": findings},
                     indent=2, default=str))
    return 1 if any(f["state"] == "verification-off" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
