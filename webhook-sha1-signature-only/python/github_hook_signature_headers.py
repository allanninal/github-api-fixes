"""Report which webhook signature header your receiver actually verifies.

Read only in both senses. Every API call is a GET, and the local source scan
opens files for reading and prints line numbers rather than lines.

GitHub sends two signature headers on every delivery from a hook that has a
secret set:

    X-Hub-Signature        HMAC-SHA1, kept for backwards compatibility
    X-Hub-Signature-256    HMAC-SHA256, the one to verify

Which of them your receiver checks is a decision in your own code. No API read
can see it, so this script establishes from the API that both headers were
sent, then searches your receiver's source for the header names and reports
what it found. That is a proxy and it is described as one: a header name built
at runtime, read from configuration or hidden inside a framework helper is
invisible to a text search.

The one subtlety worth the code: "x-hub-signature" is a prefix of
"x-hub-signature-256", so a plain substring search reports every correct
receiver as a legacy one. The modern name is removed from each line before the
legacy name is looked for.

The secret is never printed. config.secret comes back masked and this script
reports its presence only.

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
log = logging.getLogger("github_hook_signature_headers")

API = "https://api.github.com"
UA = "github-hook-signature-headers/1.0"

MODERN = "x-hub-signature-256"
LEGACY = "x-hub-signature"

# Source files worth opening. Everything else in a repository is noise, and a
# scan that opens every file is a scan that reads secrets out of .env by
# accident.
SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".rb", ".go", ".php",
            ".java", ".kt", ".cs", ".rs", ".ex", ".exs")

# States that mean the reader has something to change.
FINDINGS = ("sha1-only", "both-accepted", "no-verification-found")


def normalized(text):
    """Lower-cased, with underscores folded to hyphens. Pure.

    The same header reaches code as X-Hub-Signature-256, x-hub-signature-256
    and HTTP_X_HUB_SIGNATURE_256 depending on the runtime, and all three should
    count as the same reference.
    """
    return str(text or "").lower().replace("_", "-")


def secret_state(hook):
    """Whether the hook has a secret set: set, absent or unknown. Pure.

    The value is masked by GitHub and is never returned by this function. A set
    secret is a key present in config; an unset one is a key that is not there
    at all, which is a different note.
    """
    if not isinstance(hook, dict):
        return "unknown"
    config = hook.get("config")
    if not isinstance(config, dict):
        return "unknown"
    return "set" if "secret" in config else "absent"


def redacted_config(config):
    """A copy of a hook config safe to print. Pure.

    The secret arrives masked, so printing it would leak nothing today. It is
    replaced anyway, because the guarantee this section makes is about what the
    script can emit rather than about what GitHub happens to send.
    """
    if not isinstance(config, dict):
        return {}
    out = dict(config)
    if "secret" in out:
        out["secret"] = "<set>"
    return out


def header_names(headers):
    """Normalised header names from a delivery record. Pure. Values discarded.

    request.headers is an object, but the same data reaches this function as a
    list of name/value pairs or as raw header lines depending on what stored
    it, so all three shapes are accepted and only the names survive.
    """
    names = []
    if isinstance(headers, dict):
        names = list(headers.keys())
    elif isinstance(headers, list):
        for row in headers:
            if isinstance(row, dict) and row.get("name"):
                names.append(row["name"])
            elif isinstance(row, str) and ":" in row:
                names.append(row.split(":", 1)[0])
            elif isinstance(row, str):
                names.append(row)
    return [normalized(n).strip() for n in names]


def signature_headers(headers):
    """Which signature headers GitHub sent on a delivery. Pure.

    Exact name matching, so the prefix problem does not arise here; it arises
    in the source scan below, where the names appear inside other text.
    """
    names = header_names(headers)
    return {"sha256": MODERN in names, "sha1": LEGACY in names}


def scan_line(line):
    """Which signature header names a single line refers to. Pure.

    The modern name is removed first. Looking for the legacy name in the raw
    line instead finds a hit inside every correct reference, which turns a
    passing receiver into a finding on every line where it is right.
    """
    norm = normalized(line)
    kinds = []
    if MODERN in norm:
        kinds.append("sha256")
        norm = norm.replace(MODERN, " ")
    if LEGACY in norm:
        kinds.append("sha1")
    return kinds


def scan_source(text, path="<source>"):
    """Every signature header reference in a file, as (path, line, kind). Pure.

    Line numbers only. The contents of a line that handles signatures are the
    contents most likely to sit next to a secret, and a diagnostic that pastes
    them into a terminal or a CI log has made the problem worse.
    """
    hits = []
    for number, line in enumerate(str(text or "").splitlines(), start=1):
        for kind in scan_line(line):
            hits.append((path, number, kind))
    return hits


def receiver_state(hits):
    """What the scan says the receiver names. Pure.

    none means the header names do not appear in the source that was scanned,
    which is not the same as "does not verify" and is not reported as if it
    were.
    """
    kinds = {kind for _, _, kind in hits or []}
    if not kinds:
        return "none"
    if kinds == {"sha256"}:
        return "sha256-only"
    if kinds == {"sha1"}:
        return "sha1-only"
    return "both"


def format_hit(hit):
    """One line of the scan report. Pure. Never includes source text."""
    path, number, kind = hit
    name = "X-Hub-Signature-256" if kind == "sha256" else "X-Hub-Signature"
    label = "modern" if kind == "sha256" else "legacy"
    return "%s:%d %s %s" % (path, number, label, name)


def verdict(secret, sig=None, receiver=None):
    """Combine the API evidence and the source scan into a finding. Pure.

    secret is the output of secret_state, sig the output of signature_headers
    or None when no delivery was read, receiver the output of receiver_state or
    None when no source was scanned.
    """
    if secret == "absent":
        return ("no-secret",
                "this hook has no secret, so GitHub sends neither signature "
                "header and there is nothing for the receiver to verify. That "
                "is a different and larger problem than which digest you use.")
    if sig is not None and not sig.get("sha256") and not sig.get("sha1"):
        return ("headers-missing",
                "the delivery that was read carries no signature header at "
                "all. Either it predates the secret being set, or the record "
                "is not a delivery from this hook.")
    if receiver is None:
        return ("not-scanned",
                "GitHub sent the SHA-256 header. Which header the receiver "
                "verifies is not visible from the API, so point the scan at "
                "the receiver's source to get an answer rather than a "
                "recommendation.")
    if receiver == "none":
        return ("no-verification-found",
                "neither signature header name appears in the source that was "
                "scanned. Either the receiver does not verify, or it builds "
                "the header name at runtime, or the verification lives "
                "somewhere the scan was not pointed at.")
    if receiver == "sha1-only":
        return ("sha1-only",
                "the receiver names only the legacy SHA-1 header. GitHub sent "
                "the SHA-256 header on the same request and it is being "
                "ignored.")
    if receiver == "both":
        return ("both-accepted",
                "the receiver names both headers. A receiver that accepts "
                "either is exactly as strong as the weaker one, so this is a "
                "migration state rather than a finished one.")
    return ("sha256-only",
            "the receiver names only X-Hub-Signature-256, which is the header "
            "to verify.")


def repair(state):
    """The change to make, in the reader's own code. Pure."""
    if state == "no-secret":
        return ("set a secret on the hook first. Until there is one, GitHub "
                "sends no signature and no digest choice matters.")
    if state == "headers-missing":
        return ("read a delivery from after the secret was set, then re-run.")
    if state == "not-scanned":
        return ("re-run with --receiver pointed at the source tree that "
                "handles the webhook.")
    if state == "no-verification-found":
        return ("confirm by hand that the receiver verifies at all. If it does "
                "not, verify X-Hub-Signature-256 over the raw request bytes "
                "with a constant-time comparison and reject a request whose "
                "header is missing.")
    if state in ("sha1-only", "both-accepted"):
        return ("verify X-Hub-Signature-256 over the raw request bytes with a "
                "constant-time comparison, then delete the SHA-1 branch rather "
                "than keeping it as a fallback.")
    return "nothing. This receiver verifies the header GitHub wants it to."


def scan_paths(paths, suffixes=SUFFIXES, max_bytes=2_000_000):
    """Walk files and directories, scanning source for header references.

    Read only: files are opened for reading and only line numbers leave this
    function.
    """
    hits = []
    scanned = []
    for root in paths or []:
        for path in _walk(root, suffixes):
            try:
                if os.path.getsize(path) > max_bytes:
                    continue
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError as exc:
                log.warning("could not read %s: %s", path, exc)
                continue
            scanned.append(path)
            hits.extend(scan_source(text, path))
    return hits, scanned


def _walk(root, suffixes):
    """Every candidate source file under a path."""
    if os.path.isfile(root):
        return [root]
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in
                   (".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build")]
        for name in files:
            if name.endswith(suffixes):
                out.append(os.path.join(base, name))
    return out


def get(session, path):
    """One GET. Returns (status, json-or-None)."""
    url = API + path if path.startswith("/") else path
    r = session.get(url, timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body


def latest_delivery_headers(session, repo, hook_id):
    """The request headers of the most recent delivery, or None. Read only."""
    status, rows = get(session, "/repos/%s/hooks/%s/deliveries?per_page=10"
                       % (repo, hook_id))
    if status != 200 or not isinstance(rows, list) or not rows:
        return None
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        status, body = get(session, "/repos/%s/hooks/%s/deliveries/%s"
                           % (repo, hook_id, row["id"]))
        if status == 200 and isinstance(body, dict):
            request = body.get("request")
            if isinstance(request, dict):
                return request.get("headers")
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--hook-id", help="one hook; omit to check every hook")
    ap.add_argument("--receiver", action="append", default=[],
                    help="file or directory to scan for header names; repeatable")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN to a read-only token that can see the "
                  "repository's hooks")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    status, hooks = get(session, "/repos/%s/hooks?per_page=100" % args.repo)
    if status != 200 or not isinstance(hooks, list):
        log.error("GET /repos/%s/hooks returned %d", args.repo, status)
        return 2
    if args.hook_id:
        hooks = [h for h in hooks if str(h.get("id")) == str(args.hook_id)]

    hits, scanned = scan_paths(args.receiver)
    state_of_receiver = receiver_state(hits) if args.receiver else None
    if args.receiver:
        log.info("source scan: %d reference(s) across %d file(s)",
                 len(hits), len(set(p for p, _, _ in hits)))
        for hit in hits:
            log.info("  %s", format_hit(hit))

    findings = []
    for hook in hooks:
        secret = secret_state(hook)
        headers = None
        if secret == "set":
            headers = latest_delivery_headers(session, args.repo, hook.get("id"))
        sig = signature_headers(headers) if headers is not None else None
        state, detail = verdict(secret, sig, state_of_receiver)
        log.info("hook %s: secret is %s, %s", hook.get("id"), secret,
                 "GitHub sent both signature headers"
                 if sig and sig["sha1"] and sig["sha256"]
                 else "no delivery headers were read")
        log.info("%s: %s", state, detail)
        log.info("repair: %s", repair(state))
        findings.append({"hook_id": hook.get("id"), "secret": secret,
                         "signature_headers": sig, "state": state,
                         "detail": detail,
                         "config": redacted_config(hook.get("config"))})

    print(json.dumps({"repo": args.repo, "files_scanned": len(scanned),
                      "references": [format_hit(h) for h in hits],
                      "findings": findings}, indent=2, default=str))
    return 1 if any(f["state"] in FINDINGS for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
