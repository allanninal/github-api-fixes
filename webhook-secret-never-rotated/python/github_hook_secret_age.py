"""Say how long a webhook secret has gone without being rotated.

Read only. One GET per scope: the repository hook list, and the organization
hook list where the caller owns the organization. Nothing is changed, and no
secret value is ever read, held or printed.

This is the opposite finding to a hook with no secret. Here a secret is set and
working; the question is its age. The API will not answer that: config.secret is
masked and nothing dates it. updated_at on the hook is the only clock available,
and it moves on any edit, which makes it conclusive in exactly one direction. An
old timestamp proves no rotation. A recent one proves an edit and nothing else.

Where the caller can say when they last rotated, one more thing is checkable: a
claimed rotation that predates the hook's own updated_at never reached GitHub.

Environment:

    GITHUB_TOKEN   a read-only token with access to the repository
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_hook_secret_age")

API = "https://api.github.com"
UA = "github-hook-secret-age/1.0"

# No published guidance exists for how often a webhook secret should be rotated,
# so this is a policy number rather than a fact. The value of the check is that
# somebody chose one.
DEFAULT_MAX_AGE_DAYS = 180
# created_at and updated_at written by the same request can differ by a second.
UNEDITED_TOLERANCE_SECONDS = 60


def secret_state(config):
    """Whether a secret is configured. Never returns the value. Pure.

    GitHub masks a set secret and omits the key when there is none, so presence
    is the only readable fact and it is the only one this function reports.
    """
    if not isinstance(config, dict):
        return "unknown"
    return "set" if config.get("secret") is not None else "absent"


def redact(config):
    """A copy of a hook config that is safe to print. Pure.

    The masked value never needs to leave the response, and building the report
    from this rather than from the raw config means a future change to what the
    API returns cannot turn into a secret in a log file.
    """
    if not isinstance(config, dict):
        return {}
    safe = {k: v for k, v in config.items() if k != "secret"}
    safe["secret"] = secret_state(config)
    return safe


def parse_time(text):
    """An ISO 8601 timestamp as an aware datetime, or None. Pure."""
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def age_days(text, now):
    """Whole days between a timestamp and now, or None. Pure."""
    moment = parse_time(text)
    if moment is None or now is None:
        return None
    return int((now - moment).total_seconds() // 86400)


def unedited_since_creation(created_at, updated_at):
    """Whether the hook is exactly as it was created. Pure."""
    created, updated = parse_time(created_at), parse_time(updated_at)
    if created is None or updated is None:
        return False
    return abs((updated - created).total_seconds()) <= UNEDITED_TOLERANCE_SECONDS


def evidence_direction(age, threshold):
    """What the age of the last edit actually proves. Pure.

    These are not two grades of the same answer. Past the threshold the age is a
    lower bound on the secret's age and the finding stands on its own. Inside it,
    the edit could have been the URL, and the honest report is that nothing is
    known. Calling that second case healthy is the mistake this exists to avoid.
    """
    if age is None:
        return "unknown"
    return "conclusive" if age >= int(threshold) else "inconclusive"


def reconcile(updated_at, claimed, now=None):
    """Compare a claimed rotation date against the hook's own timestamp. Pure."""
    claim = parse_time(claimed)
    updated = parse_time(updated_at)
    if claim is None or updated is None:
        return "unknown"
    if updated < claim:
        return "not-applied"
    return "consistent"


def verdict(config, created_at, updated_at, now, threshold=DEFAULT_MAX_AGE_DAYS,
            claimed=None):
    """Turn presence, age and any claimed rotation into a finding. Pure."""
    state = secret_state(config)
    if state != "set":
        return ("no-secret",
                "this hook has no secret at all, so there is nothing to rotate "
                "and every delivery arrives unsigned. That is a different and "
                "larger finding than this one.")
    age = age_days(updated_at, now)
    if age is None:
        return ("age-unknown",
                "a secret is set, but updated_at could not be read, so nothing "
                "about its age can be established from here.")
    if claimed:
        agreement = reconcile(updated_at, claimed)
        if agreement == "not-applied":
            return ("rotation-not-applied",
                    "the record claims a rotation on %s, but the hook has not "
                    "been edited since %s. Changing a secret is an edit, so "
                    "whatever was rotated, it was not this hook."
                    % (str(claimed)[:10], str(updated_at)[:10]))
    origin = ("created_at and updated_at agree, so this is the secret the hook "
              "was created with." if unedited_since_creation(created_at, updated_at)
              else "the hook has been edited since it was created, though not "
                   "necessarily its secret.")
    if evidence_direction(age, threshold) == "conclusive":
        return ("overdue",
                "the hook has not been edited for %d days, so its secret has "
                "not been rotated for at least that long. %s" % (age, origin))
    return ("inconclusive",
            "the hook was edited %d days ago, which is inside the rotation "
            "interval, but an edit is not a rotation: updated_at moves for a "
            "URL change too. This is unknown rather than compliant." % age)


def repair(state):
    """The sentence a reader has to act on. Pure."""
    if state in ("overdue", "rotation-not-applied"):
        return ("rotate with an overlap window: teach the receiver to accept a "
                "signature from the old or the new secret, deploy that, change "
                "the secret on GitHub, then drop the old value once deliveries "
                "have settled. A straight swap loses whatever is in flight.")
    if state == "inconclusive":
        return ("record rotations somewhere the next person can read, and run "
                "this again with that date. The API cannot date a secret, so a "
                "written record is the only thing that turns this into an answer.")
    if state == "no-secret":
        return ("set a secret on the hook and verify X-Hub-Signature-256 in the "
                "receiver. Age is not the problem here.")
    if state == "age-unknown":
        return "read created_at and updated_at on the hook by hand."
    return "nothing."


def get(session, path):
    """One GET. Returns (status, json-or-None)."""
    r = session.get(API + path, timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body


def hooks_at(session, path, label):
    """Every hook at one scope."""
    status, body = get(session, path)
    if status != 200 or not isinstance(body, list):
        log.info("GET %s returned %d; %s hooks are not readable with this token",
                 path, status, label)
        return []
    return body


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPO"),
                    help="owner/name of the repository holding the hook")
    ap.add_argument("--org", default=os.environ.get("GITHUB_ORG"),
                    help="organization to audit as well, if you can read it")
    ap.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS,
                    help="your rotation interval. There is no published one")
    ap.add_argument("--rotated-on", default=None,
                    help="the date your records claim the secret was last "
                         "rotated, as YYYY-MM-DD")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN to a read-only token")
        return 2
    if not args.repo and not args.org:
        log.error("set --repo, --org, or both")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    now = datetime.now(timezone.utc)
    scopes = []
    if args.repo:
        scopes.append(("/repos/%s/hooks?per_page=100" % args.repo, args.repo))
    if args.org:
        scopes.append(("/orgs/%s/hooks?per_page=100" % args.org, args.org))

    report = []
    findings = 0
    for path, label in scopes:
        for hook in hooks_at(session, path, label):
            config = hook.get("config") or {}
            safe = redact(config)
            log.info("hook %s %s secret=%s", hook.get("id"), safe.get("url"),
                     safe.get("secret"))
            log.info("created %s, updated %s, %s",
                     str(hook.get("created_at"))[:10], str(hook.get("updated_at"))[:10],
                     "unedited since creation"
                     if unedited_since_creation(hook.get("created_at"),
                                                hook.get("updated_at"))
                     else "edited since creation")
            state, detail = verdict(config, hook.get("created_at"),
                                    hook.get("updated_at"), now,
                                    args.max_age_days, args.rotated_on)
            log.info("%s: %s", state, detail)
            log.info("repair: %s", repair(state))
            if state in ("overdue", "rotation-not-applied", "no-secret"):
                findings += 1
            report.append({
                "scope": label,
                "hook_id": hook.get("id"),
                "config": safe,
                "created_at": hook.get("created_at"),
                "updated_at": hook.get("updated_at"),
                "days_since_edit": age_days(hook.get("updated_at"), now),
                "unedited_since_creation": unedited_since_creation(
                    hook.get("created_at"), hook.get("updated_at")),
                "evidence": evidence_direction(
                    age_days(hook.get("updated_at"), now), args.max_age_days),
                "rotation_record": reconcile(hook.get("updated_at"), args.rotated_on),
                "state": state,
                "detail": detail,
                "repair": repair(state),
            })

    print(json.dumps({"rotation_interval_days": args.max_age_days,
                      "hooks": report}, indent=2, default=str))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
