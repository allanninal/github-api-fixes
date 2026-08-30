"""Find webhooks that deliver over plaintext HTTP, and say which ones leak.

Read only. Every call is a GET. Changing a hook's URL is a write and this
script does not do it: it prints the change, as a full config rather than a
single field, because a webhook's config is replaced rather than merged and the
secret you read back is a mask.

GitHub will deliver to an http:// URL without complaint. The payload and the
signature header that authenticates it both cross the network as plain text. A
signature proves the payload came from GitHub; it conceals nothing and it can
be replayed by anyone who saw it.

Two things that look the same in this field are kept apart:

    http:// on a routable host      payloads are readable in transit
    http:// on a private address    GitHub cannot route there at all, so this
                                    hook has never delivered anything

And one thing that hides here is named: insecure_ssl reads "0" on a plaintext
hook, because there is no certificate to verify when there is no TLS, so the
field an audit samples reports the reassuring value.

The secret is never printed, and neither is any query string or userinfo in a
hook URL.

Environment:

    GITHUB_TOKEN    a read-only token that can see the repository's hooks
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_hook_transport")

API = "https://api.github.com"
UA = "github-hook-transport/1.0"

# Names that never resolve to somewhere GitHub can deliver.
LOCAL_NAMES = ("localhost", "localhost.localdomain", "ip6-localhost")
LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa",
                  ".localdomain")

# States that mean payloads are readable in transit.
LEAKING = ("plaintext",)


def config_of(hook):
    """The config object of a hook, or an empty dict. Pure."""
    if not isinstance(hook, dict):
        return {}
    config = hook.get("config")
    return config if isinstance(config, dict) else {}


def raw_url(hook):
    """The configured URL, trimmed, or "". Pure."""
    return str(config_of(hook).get("url") or "").strip()


def safe_url(url):
    """A URL with its query string and any userinfo removed. Pure.

    This script prints URLs, and those are the two places a credential hides in
    one: a token in a query parameter and a user:pass pair before the host.
    """
    text = str(url or "").strip()
    if not text:
        return ""
    text = text.split("?", 1)[0].split("#", 1)[0]
    if "://" in text:
        scheme, rest = text.split("://", 1)
    else:
        scheme, rest = "", text
    if "@" in rest:
        rest = rest.rsplit("@", 1)[1]
        rest = "<redacted>@" + rest
    return ("%s://%s" % (scheme, rest)) if scheme else rest


def scheme_of(url):
    """The lower-cased scheme of a URL, or "". Pure."""
    text = str(url or "").strip()
    return text.split("://", 1)[0].lower() if "://" in text else ""


def host_of(url):
    """The lower-cased host of a URL, without port or userinfo. Pure."""
    text = str(url or "").strip()
    rest = text.split("://", 1)[1] if "://" in text else text
    rest = rest.split("/", 1)[0]
    if "@" in rest:
        rest = rest.rsplit("@", 1)[1]
    if rest.startswith("["):
        return rest[1:].split("]", 1)[0].lower()
    return rest.split(":", 1)[0].lower()


def is_private_host(host):
    """Whether a host is somewhere GitHub's delivery network cannot reach. Pure.

    Deliberately a name and address test rather than a DNS lookup. A resolver
    inside your network answers differently from GitHub's, so resolving here
    would produce an answer about the wrong network, and a script that makes
    DNS queries for every hook in an organization is a different kind of tool.
    """
    name = str(host or "").strip().lower().strip(".")
    if not name:
        return False
    if name in LOCAL_NAMES or name.endswith(LOCAL_SUFFIXES):
        return True
    if name in ("::1", "0:0:0:0:0:0:0:1"):
        return True
    if name.startswith(("fd", "fc", "fe80:")) and ":" in name:
        return True
    parts = name.split(".")
    if len(parts) == 4 and all(p.isdigit() and len(p) <= 3 for p in parts):
        octets = [int(p) for p in parts]
        if any(o > 255 for o in octets):
            return False
        if octets[0] in (0, 127, 10):
            return True
        if octets[0] == 192 and octets[1] == 168:
            return True
        if octets[0] == 172 and 16 <= octets[1] <= 31:
            return True
        if octets[0] == 169 and octets[1] == 254:
            return True
    return False


def insecure_ssl_reads(hook):
    """The insecure_ssl value as text, or "" when absent. Pure.

    Read as text on purpose. This script does not interpret the flag - that is
    the certificate-verification question - it only needs to report what an
    audit sampling the field would have seen.
    """
    config = config_of(hook)
    if "insecure_ssl" not in config:
        return ""
    return str(config["insecure_ssl"]).strip().lower()


def looks_compliant(hook):
    """Whether a plaintext hook reads as safe on the field audits sample. Pure.

    The whole reason this problem survives a review that was genuinely carried
    out: insecure_ssl is "0" on a hook with no TLS, because there is no
    certificate to verify.
    """
    return (scheme_of(raw_url(hook)) not in ("https", "")
            and insecure_ssl_reads(hook) in ("0", "false"))


def has_secret(hook):
    """Whether the hook has a secret set. Pure. The value is never read."""
    return "secret" in config_of(hook)


def classify(hook):
    """Sort one hook into a state and a sentence. Pure."""
    ident = "hook %s" % (hook.get("id", "?") if isinstance(hook, dict) else "?")
    url = raw_url(hook)
    scheme = scheme_of(url)
    if not url or not scheme:
        return ("no-scheme",
                "%s has no usable URL in its config, so nothing can be said "
                "about how it delivers." % ident)
    if scheme == "https":
        if insecure_ssl_reads(hook) in ("1", "true"):
            return ("encrypted-unverified",
                    "%s posts to %s over TLS, but with certificate "
                    "verification disabled. The transport is encrypted and "
                    "unauthenticated, which is a different question from this "
                    "one." % (ident, safe_url(url)))
        return ("encrypted",
                "%s posts to %s over TLS." % (ident, safe_url(url)))
    if scheme != "http":
        return ("unknown-scheme",
                "%s posts to a %s:// URL, which is not a scheme GitHub "
                "delivers to. Read the URL by hand." % (ident, scheme))
    if is_private_host(host_of(url)):
        return ("plaintext-unreachable",
                "%s posts to %s, which GitHub cannot route to. This hook has "
                "never delivered anything, and it is not leaking payloads "
                "either." % (ident, safe_url(url)))
    suffix = (" insecure_ssl reads \"%s\", which is what a hook with no TLS "
              "at all always reads." % insecure_ssl_reads(hook)
              if looks_compliant(hook) else "")
    return ("plaintext",
            "%s posts to %s over an unencrypted connection.%s"
            % (ident, safe_url(url), suffix))


def repair(state, hook):
    """The change to make, printed as a whole config. Pure."""
    if state == "plaintext":
        rotate = (" and a new secret. Rotate: that secret has been signing "
                  "payloads on an open channel." if has_secret(hook)
                  else " and a secret, since this hook has none.")
        return ("move the receiver behind HTTPS, then send the hook's full "
                "config with the new URL, the content type%s The config is "
                "replaced, not merged, and the secret you read back is a mask."
                % rotate)
    if state == "plaintext-unreachable":
        return ("delete this hook, or point it at an endpoint GitHub can "
                "reach over HTTPS. Its delivery log will be connection errors "
                "and timeouts for as far back as the retention window goes.")
    if state == "encrypted-unverified":
        return ("this is the certificate-verification question rather than "
                "the transport one. Fix the certificate, then set insecure_ssl "
                "back to \"0\" as part of a full config update.")
    if state in ("no-scheme", "unknown-scheme"):
        return ("read the hook's URL by hand. A hook GitHub cannot parse a "
                "scheme from is not delivering anything.")
    return "nothing. This hook delivers over TLS."


def summarize(hooks):
    """Counts across every hook read. Pure."""
    rows = [h for h in (hooks or []) if isinstance(h, dict)]
    states = [classify(h)[0] for h in rows]
    return {"total": len(rows),
            "plaintext": states.count("plaintext"),
            "unreachable": states.count("plaintext-unreachable"),
            "encrypted": states.count("encrypted") + states.count("encrypted-unverified"),
            "unreadable": states.count("no-scheme") + states.count("unknown-scheme")}


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

    findings = []
    for scope in scopes:
        label = scope[1:] if scope.startswith("@") else scope
        hooks = list_hooks(session, scope)
        stats = summarize(hooks)
        log.info("%d hook(s) on %s", stats["total"], label)
        for hook in hooks:
            state, detail = classify(hook)
            findings.append({"scope": label, "hook_id": hook.get("id"),
                             "state": state, "detail": detail,
                             "url": safe_url(raw_url(hook)),
                             "looks_compliant": looks_compliant(hook)})
            if state != "encrypted":
                log.info("%s: %s", state, detail)
                log.info("repair: %s", repair(state, hook))
        if stats["plaintext"] == 0:
            log.info("encrypted: no hook on %s delivers over plaintext HTTP "
                     "to a routable host", label)

    print(json.dumps({"scopes": scopes, "findings": findings},
                     indent=2, default=str))
    return 1 if any(f["state"] in LEAKING for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
