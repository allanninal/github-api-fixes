"""Find GitHub credentials sitting in URLs, without ever printing one.

Read only, and one request: GET /rate_limit with the credential in an
Authorization header. That call spends no quota and answers the only question
the API can answer here, which is whether the credential you are holding still
works.

Two things this deliberately does not do. It never issues a request with a
credential in the query string, not even to reproduce the documented
anonymous-tier reading, because doing so writes a fresh copy of the secret into
every log between here and GitHub. And it never emits a credential value: a
finding carries a shape, a length and a truncated digest, all of which are safe
to paste into a ticket.
"""
import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_token_in_url")

API = "https://api.github.com"
UA = "github-token-in-url/1.0"

# Documented token prefixes. Naming the shape tells you which credential to
# revoke; it is not itself a secret.
PREFIXES = (
    ("github_pat_", "fine-grained-pat"),
    ("ghp_", "classic-pat"),
    ("gho_", "oauth-token"),
    ("ghu_", "app-user-token"),
    ("ghs_", "app-installation-token"),
    ("ghr_", "refresh-token"),
)
LEGACY_HEX = re.compile(r"^[0-9a-f]{40}$")

# Parameter names that carry a credential whatever the value looks like.
SUSPECT_NAMES = {"access_token", "token", "oauth_token", "api_key", "apikey",
                 "client_secret", "private_token", "auth", "password", "secret"}

# Parameter names whose values are legitimately forty hex characters. Without
# these, every commit SHA in a URL is reported as a legacy token and the report
# becomes noise nobody reads.
GIT_OBJECT_NAMES = {"sha", "commit_sha", "head_sha", "base_sha", "tree_sha",
                    "oid", "ref", "base", "head"}

URL_PATTERN = re.compile(r"https?://[^\s<>]+")

# Punctuation a log line puts after a URL. Stripped so a quoted URL does not
# carry its closing quote into the audit.
TRAILING = '"' + "'" + ">),.;"

REDACTED = "REDACTED"


def shape_of(value):
    """Name the kind of credential a string looks like. Pure.

    Returns a shape name, never the value. `opaque` means "long enough to be a
    secret and not in a documented form", which is worth reporting when it sits
    in a parameter called access_token.
    """
    text = str(value or "")
    for prefix, name in PREFIXES:
        if text.startswith(prefix):
            return name
    if LEGACY_HEX.match(text):
        return "legacy-hex40"
    return "opaque" if len(text) >= 16 else "short"


def fingerprint(value):
    """A twelve-character digest, for correlating two sightings. Pure.

    Truncated on purpose: enough to say "these two log lines carry the same
    credential", not enough to be a credential.
    """
    digest = hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()
    return "sha256:" + digest[:12]


def same_credential(left, right):
    """Do two fingerprints describe the same value? Pure."""
    if not left or not right:
        return False
    return hmac.compare_digest(str(left), str(right))


def urls_in(text):
    """Every URL in a blob of log or configuration. Pure."""
    return [u.rstrip(TRAILING) for u in URL_PATTERN.findall(str(text or ""))]


def is_credential(name, value):
    """Would this query parameter be reported as carrying a credential? Pure.

    The single place that decision is made, so the reporter and the redactor
    cannot drift apart and start disagreeing about what counts as a secret.
    """
    lowered = str(name or "").lower()
    if lowered in SUSPECT_NAMES:
        return True
    if lowered in GIT_OBJECT_NAMES:
        return False
    return shape_of(value) not in ("short", "opaque")


def credential_params(url):
    """Credential-bearing query parameters in one URL. Pure.

    A parameter is reported when its name is a known credential name or when
    its value is shaped like a token, so a credential hiding under a harmless
    name is still found. The value is never in the return.
    """
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return []
    out = []
    for name, value in parse_qsl(parts.query, keep_blank_values=True):
        if not is_credential(name, value):
            continue
        out.append({"param": name, "shape": shape_of(value), "length": len(value),
                    "fingerprint": fingerprint(value),
                    "ignored_by_github": name.lower() == "access_token"})
    return out


def redact(url):
    """The same URL with every credential-bearing value replaced. Pure.

    This is the artefact you paste into a ticket. Anything that would be
    reported by credential_params is replaced, because both ask is_credential.
    """
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return REDACTED
    if not parts.query:
        return str(url or "")
    pairs = [(name, REDACTED if is_credential(name, value) else value)
             for name, value in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(pairs), parts.fragment))


def audit(entries):
    """Findings across many labelled URLs, with the redacted form attached. Pure.

    entries: [(label, url), ...] where label is a line number, a file name or
    anything else that helps somebody find the copy again.
    """
    findings = []
    for label, url in entries or []:
        for hit in credential_params(url):
            item = dict(hit)
            item["where"] = label
            item["redacted"] = redact(url)
            findings.append(item)
    return findings


def verdict(findings, live, held_fingerprint):
    """Turn the findings into a decision about revocation. Pure."""
    if not findings:
        return ("no-credential-in-url",
                "no query parameter carried a credential-shaped value.")

    matched = [f for f in findings
               if same_credential(f.get("fingerprint"), held_fingerprint)]
    ignored = [f for f in findings if f.get("ignored_by_github")]
    distinct = len({f.get("fingerprint") for f in findings})

    tail = (" %d of them use access_token, which GitHub ignores outright, so "
            "those requests went out anonymous rather than authenticated."
            % len(ignored)) if ignored else ""

    if matched and live:
        return ("live-credential-in-url",
                "%d occurrence(s) of %d distinct credential(s) in URLs, and one "
                "of them is the credential this process is holding, which still "
                "authenticates. Revoke it; relocating it to a header does not "
                "unwrite the log lines.%s" % (len(findings), distinct, tail))
    if matched:
        return ("dead-credential-in-url",
                "%d occurrence(s) in URLs match the credential this process "
                "holds, and that credential no longer authenticates. The "
                "exposure is historical, but the habit that created it is "
                "not.%s" % (len(findings), tail))
    return ("credential-in-url",
            "%d occurrence(s) of %d distinct credential-shaped value(s) in "
            "URLs. None match the credential this process holds, so their "
            "liveness cannot be judged from here; treat them as live until "
            "somebody proves otherwise.%s" % (len(findings), distinct, tail))


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
    ap.add_argument("urls", nargs="*", help="URLs to audit")
    ap.add_argument("--from-file",
                    help="a log or config file; every URL in it is audited")
    args = ap.parse_args()

    entries = [("argv[%d]" % i, u) for i, u in enumerate(args.urls, start=1)]
    if args.from_file:
        with open(args.from_file, "r", encoding="utf-8", errors="replace") as fh:
            for number, line in enumerate(fh, start=1):
                for url in urls_in(line):
                    entries.append(("%s:%d" % (args.from_file, number), url))

    token = os.environ.get("GITHUB_TOKEN")
    held_fingerprint, live = None, False
    if token:
        held_fingerprint = fingerprint(token)
        session = requests.Session()
        session.headers.update({
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": UA,
        })
        # In the header, never in the URL. GET /rate_limit spends no quota.
        status, _ = get(session, "/rate_limit")
        live = status == 200
        log.info("credential in this process: %s, %s", held_fingerprint,
                 "still live" if live else "not accepted (status %s)" % status)
    else:
        log.info("set GITHUB_TOKEN to also learn whether a credential found in "
                 "a URL is the one you are holding, and whether it still works")

    log.info("scanned %d url(s)", len(entries))
    findings = audit(entries)
    for item in findings:
        log.warning("%s carries %s (%s, %d chars) in ?%s= ; scrubbed: %s",
                    item["where"], item["fingerprint"], item["shape"],
                    item["length"], item["param"], item["redacted"])

    state, detail = verdict(findings, live, held_fingerprint)
    log.info("%s: %s", state, detail)

    if findings:
        log.info("repair: move the credential into Authorization: Bearer TOKEN "
                 "on every request, including inside any client wrapper that "
                 "appends parameters for you.")
        log.info("repair: revoke and re-mint before scrubbing. Revocation takes "
                 "seconds and log retention takes days.")
        log.info("note: this script cannot enumerate where a URL has already "
                 "been written, and it will not reproduce the leak to measure "
                 "it. The 60-versus-5000 reading belongs to the anonymous-tier "
                 "check, with the header removed rather than the token moved.")

    print(json.dumps({"scanned": len(entries), "findings": findings,
                      "state": state}, indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
