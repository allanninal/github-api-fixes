"""Check whether a repository's deploy keys can do what its automation needs.

Read only. One GET per repository, nothing is written, and the keys are never
exercised: no push is attempted to find out whether a key can push. The
capability is declared on the key object as read_only, which is the same fact
one request earlier and without changing anybody's repository.

The point of the note: a deploy key's read_only flag is chosen when the key is
created and cannot be edited afterwards. Read-only is the default and is right
for almost every use, so a key added for a CI job that reads works perfectly
until somebody adds a push step. The refusal then arrives from Git over SSH
rather than from the API, so the diagnosis starts in the wrong tool, and no
scope, App permission or token change moves it.

The public key material is dropped before anything is printed. What this
reports is ids, titles, the boolean and the dates.

What this can and cannot see: the keys endpoint needs repository admin, so a
token without it gets a refusal, which is reported as unreadable rather than as
"no keys" -- those are different findings. Which key your SSH client actually
presents is invisible from here; the declared capability of every key on the
repository is not.

Environment:

    GITHUB_TOKEN    a token with admin read on the repository, for the keys
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_deploy_key_capability")

API = "https://api.github.com"
UA = "github-deploy-key-capability/1.0"

# The only fields that leave this script. The listing also carries the public
# key itself; it is dropped here rather than at each print site, so nothing
# downstream can put it in a log or a JSON artefact.
SAFE_FIELDS = ("id", "title", "read_only", "created_at", "verified", "added_by")

# A deploy key older than this is worth a look during the same read.
DEFAULT_MAX_AGE_DAYS = 365


def redact(key):
    """One deploy key reduced to metadata. Pure. Never carries key material."""
    if not isinstance(key, dict):
        return {}
    out = {}
    for field in SAFE_FIELDS:
        if field in key:
            out[field] = key[field]
    return out


def redact_all(keys):
    """The whole listing, reduced. Pure."""
    return [redact(k) for k in (keys or []) if isinstance(k, dict)]


def capability(key):
    """What this key is allowed to do, as declared. Pure."""
    if not isinstance(key, dict) or "read_only" not in key:
        return "unknown"
    return "read-only" if key.get("read_only") else "read-write"


def writable_keys(keys):
    """The ids of keys that can push. Pure."""
    return [k.get("id") for k in (keys or [])
            if isinstance(k, dict) and capability(k) == "read-write"]


def verdict(status, keys, needs_write):
    """Classify one repository's deploy keys. Pure. (state, detail).

    needs_write is a fact about your job rather than about the repository, so
    it has to be supplied. The API knows what the keys can do; it does not know
    what you were going to ask them to do.
    """
    try:
        code = int(status)
    except (TypeError, ValueError):
        code = None

    if code != 200:
        if code in (403, 404):
            return ("keys-unreadable",
                    "the deploy keys endpoint needs repository admin and this "
                    "token does not have it. That is not the same as the "
                    "repository having no keys.")
        return ("keys-unreadable",
                "the deploy keys could not be listed, so nothing here is a "
                "finding about the keys.")

    rows = [k for k in (keys or []) if isinstance(k, dict)]
    writable = writable_keys(rows)

    if not rows:
        if needs_write:
            return ("no-deploy-keys",
                    "this repository has no deploy keys at all, so a push over "
                    "SSH is authenticating with something else or not at all.")
        return ("no-deploy-keys",
                "this repository has no deploy keys, which is fine if nothing "
                "clones it over SSH.")

    if needs_write and not writable:
        return ("write-needed-none-capable",
                "this repository's automation pushes and all %d deploy key(s) "
                "on it are read-only, which is the whole failure." % len(rows))
    if needs_write:
        return ("write-capable-key-present",
                "%d of %d deploy key(s) can push, so a read-only key is not "
                "what refused this write." % (len(writable), len(rows)))
    if writable:
        return ("write-capable-but-unused",
                "%d deploy key(s) can push on a repository whose automation "
                "only reads. That is a standing grant rather than a failure."
                % len(writable))
    return ("read-only-and-correct",
            "every deploy key is read-only and nothing here needs to push, "
            "which is the recommended arrangement.")


def attribute_git_error(text):
    """Work out which credential refused a push, from the message. Pure.

    Returns (state, detail). Three of the four outcomes send the reader
    somewhere other than the deploy keys, which is the point: the same corner
    of the same build log holds four different problems.
    """
    message = str(text or "").lower()
    if not message.strip():
        return ("no-message", "nothing was supplied to attribute.")
    if "marked as read only" in message or "marked as read-only" in message:
        return ("deploy-key-read-only",
                "the message names the key itself, so the refusal is the key's "
                "declared capability and not a scope, a token or SSH.")
    if "protected branch" in message or "gh006" in message:
        return ("refused-by-branch-protection",
                "the credential was accepted and the branch refused the update. "
                "That is a rule on the ref rather than a capability problem.")
    if "archived" in message:
        return ("repository-archived",
                "the repository is archived and read-only, so no credential of "
                "any kind can write to it.")
    if "permission denied (publickey)" in message:
        return ("key-not-accepted",
                "the key was not accepted at all, so it is not on this "
                "repository or the agent presented a different one. This is "
                "authentication, not capability.")
    if "write access to repository not granted" in message:
        return ("write-not-granted",
                "the write was refused without naming the key. Over SSH that is "
                "a read-only deploy key; over HTTPS it is the token or the "
                "installation. The keys listing settles it.")
    return ("unattributed",
            "the message does not name a known refusal. List the keys anyway "
            "and check which credential the remote URL implies.")


def age_days(created_at, now=None):
    """How old a key is, in whole days. Pure. None when unreadable."""
    if not created_at:
        return None
    text = str(created_at).replace("Z", "+00:00")
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0, (now - when).days)


def stale_keys(keys, max_age_days=DEFAULT_MAX_AGE_DAYS, now=None):
    """Keys older than the rotation policy. Pure. Metadata only."""
    out = []
    for key in keys or []:
        if not isinstance(key, dict):
            continue
        age = age_days(key.get("created_at"), now)
        if age is not None and age >= max_age_days:
            row = redact(key)
            row["age_days"] = age
            out.append(row)
    return out


def repair(state):
    """The sentence a reader has to act on. Pure."""
    if state in ("write-needed-none-capable", "deploy-key-read-only"):
        return ("create a replacement deploy key with write access and delete "
                "the old one, or move the job to a GitHub App installation "
                "token with contents: write, which is scoped, expiring and "
                "auditable. read_only cannot be edited on an existing key.")
    if state == "write-capable-key-present":
        return ("look elsewhere for this refusal: a key that can push exists, "
                "so check the branch rules and the repository state.")
    if state == "write-capable-but-unused":
        return ("delete the write-capable key if nothing pushes with it. A "
                "standing write grant on a repository that only gets read is "
                "the kind of thing nobody revisits.")
    if state == "read-only-and-correct":
        return ("nothing. Read-only is the recommended default and this "
                "repository matches what its automation does.")
    if state == "no-deploy-keys":
        return ("check which credential your clone actually uses. With no "
                "deploy keys, an SSH remote is authenticating as a user rather "
                "than as the repository.")
    if state == "keys-unreadable":
        return ("run this with a token that has repository admin, or an App "
                "with administration: read. Do not record the keys as absent.")
    if state == "refused-by-branch-protection":
        return ("read the branch rules rather than the credential. The push was "
                "authorised and the ref refused it.")
    if state == "repository-archived":
        return ("skip the repository. An archived repository is read-only for "
                "every credential.")
    if state == "key-not-accepted":
        return ("fix authentication first: confirm the public key is on this "
                "repository and that the agent is presenting the matching "
                "private key.")
    if state == "write-not-granted":
        return ("check the remote URL. An SSH remote points at the deploy keys, "
                "an HTTPS one points at the token or the installation.")
    return ("list the deploy keys and read read_only before investigating SSH "
            "or scopes.")


def read_cost(repos):
    """Requests this run will spend against the core quota. Pure."""
    return len(repos or [])


def list_keys(session, full_name):
    """One GET of a repository's deploy keys. Returns (status, list)."""
    r = session.get(API + "/repos/" + full_name + "/keys?per_page=100",
                    timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed "
                         "or revoked")
    if r.status_code == 403 and "rate limit" in r.text.lower():
        raise SystemExit("403 rate limited. GET /rate_limit reports the reset "
                         "time and does not itself consume quota")
    if r.status_code != 200:
        return r.status_code, []
    try:
        body = r.json()
    except ValueError:
        return r.status_code, []
    return r.status_code, (body if isinstance(body, list) else [])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", action="append", required=True,
                    help="owner/name to check. Repeatable.")
    ap.add_argument("--needs-write", action="store_true",
                    help="this repository's automation pushes over SSH")
    ap.add_argument("--git-error", default="",
                    help="the line your build log recorded, to attribute the "
                         "refusal without reproducing it")
    ap.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS,
                    help="rotation policy for deploy keys, in days")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (read access plus repository admin for the "
                  "keys endpoint)")
        return 2

    log.info("read cost: %d request(s) per repository against the core hourly "
             "quota", 1)
    log.info("read cost: %d request(s) in total", read_cost(args.repo))

    attributed = None
    if args.git_error:
        state, detail = attribute_git_error(args.git_error)
        log.info("git error -> %s: %s", state, detail)
        log.info("repair: %s", repair(state))
        attributed = {"state": state, "detail": detail, "repair": repair(state)}

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })

    findings = []
    for name in args.repo:
        status, keys = list_keys(session, name)
        # Reduced here, once. Nothing below this line has the key material.
        rows = redact_all(keys)
        state, detail = verdict(status, rows, args.needs_write)
        stale = stale_keys(rows, args.max_age_days)

        log.info("%s: %d deploy key(s), %d of them write-capable", name,
                 len(rows), len(writable_keys(rows)))
        for row in rows:
            added_by = row.get("added_by") or "unknown"
            age = age_days(row.get("created_at"))
            log.info('  key %s "%s" %s created %s by %s%s', row.get("id"),
                     row.get("title"), capability(row),
                     str(row.get("created_at") or "")[:10], added_by,
                     ", %d day(s) old" % age if age is not None else "")
        log.info("%s: %s", state, detail)
        log.info("repair: %s", repair(state))
        if stale:
            log.info("rotation: %d key(s) older than %d day(s)", len(stale),
                     args.max_age_days)

        findings.append({
            "repository": name,
            "keys_status": status,
            "keys": rows,
            "write_capable_ids": writable_keys(rows),
            "stale_keys": stale,
            "state": state,
            "detail": detail,
            "repair": repair(state),
        })

    print(json.dumps({
        "requests_spent": read_cost(args.repo),
        "git_error": attributed,
        "findings": findings,
    }, indent=2, default=str))
    bad = {"write-needed-none-capable", "write-capable-but-unused"}
    return 1 if any(f["state"] in bad for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
