"""Say whether a GitHub webhook is switched off, and which of three ways it happened.

Read only. Every call is a GET. Re-enabling a hook is a write and this script
does not do it: it prints the request for you to run once you have decided the
endpoint can survive being switched back on.

An inactive hook is not attempted, so it produces no delivery record, no
failure and no error. That is what makes it hard to find - the delivery log is
empty rather than full of 5xx, so the evidence looks like the absence of
events rather than the absence of a hook.

Three routes lead to active: false, and they do not share a repair:

    created inactive     never delivered anything, updated_at == created_at
    toggled off later    somebody switched it off, updated_at > created_at
    disabled by GitHub   a sustained run of failures, last_response is 4xx/5xx

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
log = logging.getLogger("github_hook_active_audit")

API = "https://api.github.com"
UA = "github-hook-active-audit/1.0"

# Spellings of the boolean seen in the wild. GitHub returns a real JSON boolean,
# but the value reaches this script through whatever wrote the hook: a form
# post, a config file, a Terraform state, a client that stringifies everything.
TRUTHY = ("true", "1", "yes", "on")
FALSY = ("false", "0", "no", "off")

# States that mean a hook is delivering nothing at all.
OFF_STATES = ("inactive-after-failures", "inactive-toggled",
              "inactive-since-creation", "inactive-undated")


def active_state(hook):
    """Three-state read of the active flag: on, off or unknown. Pure.

    Deliberately not a boolean. A truthy test on this field reads the string
    "false" as on, and an absent field as off, and both of those are wrong in
    the direction that matters: one hides a dead hook, the other invents one.
    """
    if not isinstance(hook, dict) or "active" not in hook:
        return "unknown"
    raw = hook["active"]
    if isinstance(raw, bool):
        return "on" if raw else "off"
    if isinstance(raw, (int, float)):
        return "on" if raw else "off"
    text = str(raw).strip().lower()
    if text in TRUTHY:
        return "on"
    if text in FALSY:
        return "off"
    return "unknown"


def last_code(hook):
    """The status code of the most recent delivery attempt, or None. Pure.

    last_response survives the hook being switched off, which is the only
    reason this script can tell a disable from a toggle.
    """
    if not isinstance(hook, dict):
        return None
    resp = hook.get("last_response")
    if not isinstance(resp, dict):
        return None
    code = resp.get("code")
    if code is None or code == "":
        return None
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


def failed_last(hook):
    """Whether the most recent recorded response was a failure. Pure."""
    code = last_code(hook)
    return code is not None and code >= 400


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


def days_since(text, now):
    """Whole days between a timestamp and now, or None. Pure."""
    when = parsed_time(text)
    if when is None or now is None:
        return None
    return (now - when).days


def edited_after_creation(hook, tolerance_seconds=90):
    """True, False or None - was this hook changed after it was made? Pure.

    None when either timestamp is missing or unparseable, because "we cannot
    tell" is a real answer here and reporting it as "never edited" would put
    the reader in the wrong one of three repairs. The tolerance exists because
    a hook created and configured in one API call comes back with two
    timestamps a second or two apart.
    """
    if not isinstance(hook, dict):
        return None
    created = parsed_time(hook.get("created_at"))
    updated = parsed_time(hook.get("updated_at"))
    if created is None or updated is None:
        return None
    return (updated - created).total_seconds() > tolerance_seconds


def newest_delivery(deliveries):
    """The most recent delivered_at across delivery records, or None. Pure."""
    best, best_at = None, None
    for row in deliveries or []:
        if not isinstance(row, dict):
            continue
        when = parsed_time(row.get("delivered_at"))
        if when is None:
            continue
        if best_at is None or when > best_at:
            best, best_at = str(row.get("delivered_at")), when
    return best


def silent_days(deliveries, now):
    """Days since the last delivery, or None when there has never been one. Pure."""
    return days_since(newest_delivery(deliveries), now)


def classify(hook, deliveries=None, now=None):
    """Sort one hook into a state and a sentence. Pure.

    deliveries is optional and only ever corroborates. The finding lives in the
    hook record; the log dates it and says how much is still replayable.
    """
    ident = "hook %s" % (hook.get("id", "?") if isinstance(hook, dict) else "?")
    state = active_state(hook)
    if state == "unknown":
        return ("unknown",
                "%s does not report a readable active flag. Read it in the "
                "repository's settings before trusting anything else here."
                % ident)
    if state == "off":
        if failed_last(hook):
            return ("inactive-after-failures",
                    "%s is switched off, and its last recorded response was "
                    "%d. GitHub disables a hook after a sustained run of "
                    "failures, so this is an aftermath rather than a cause."
                    % (ident, last_code(hook)))
        edited = edited_after_creation(hook)
        if edited is True:
            age = days_since(hook.get("updated_at"), now)
            return ("inactive-toggled",
                    "%s is switched off and was last edited %s%s. It delivered "
                    "before that and has delivered nothing since."
                    % (ident, hook.get("updated_at", "at an unrecorded time"),
                       ", %d day(s) ago" % age if age is not None else ""))
        if edited is False:
            return ("inactive-since-creation",
                    "%s is switched off and has never been edited, so it was "
                    "created inactive and has never delivered anything."
                    % ident)
        return ("inactive-undated",
                "%s is switched off. Its timestamps are missing, so which of "
                "the three ways it got there cannot be told from here." % ident)
    quiet = silent_days(deliveries, now)
    if deliveries is not None and newest_delivery(deliveries) is None:
        return ("active-but-silent",
                "%s is switched on and the delivery log is empty. The hook is "
                "not the problem: either nothing it subscribes to has "
                "happened, or it subscribes to the wrong events." % ident)
    if quiet is not None and quiet >= 30:
        return ("active-but-quiet",
                "%s is switched on and its last delivery was %d day(s) ago."
                % (ident, quiet))
    return ("active", "%s is switched on." % ident)


def repair(state, hook, repo="OWNER/REPO"):
    """The request or the decision a reader has to make. Pure.

    Every branch that ends in a config change prints the change. Turning a hook
    back on is a deliberate act with a burst of traffic behind it, and this
    script is read only in any case.
    """
    hook_id = hook.get("id", "HOOK_ID") if isinstance(hook, dict) else "HOOK_ID"
    enable = ("gh api --method PATCH /repos/%s/hooks/%s -F active=true"
              % (repo, hook_id))
    if state == "inactive-after-failures":
        return ("fix the receiver for the recorded response code first, then "
                "re-enable with %s. Re-enabling before the receiver is fixed "
                "gets the hook disabled again and spends the retention window "
                "you need for the replay." % enable)
    if state == "inactive-toggled":
        return ("confirm the endpoint is healthy and can take a burst, then "
                "re-enable with %s." % enable)
    if state == "inactive-since-creation":
        return ("this hook has never delivered anything. Either it was made "
                "inactive by mistake, in which case %s, or it was superseded "
                "by another hook and should be deleted." % enable)
    if state == "inactive-undated":
        return ("read the delivery log for the date the silence started, then "
                "decide. When you re-enable: %s." % enable)
    if state in ("active-but-silent", "active-but-quiet"):
        return ("nothing here. The hook is on, so look at its events array "
                "and at whether anything it subscribes to has happened.")
    if state == "unknown":
        return "read the active flag in the repository's settings by hand."
    return "nothing. This hook is on."


def summarize(hooks):
    """Counts across every hook read. Pure."""
    rows = [h for h in (hooks or []) if isinstance(h, dict)]
    off = [h for h in rows if active_state(h) == "off"]
    return {"total": len(rows), "inactive": len(off),
            "active": len([h for h in rows if active_state(h) == "on"]),
            "inactive_ids": [h.get("id") for h in off]}


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
        log.error("GET %s returned %d; a token that cannot read hooks reports "
                  "no hooks rather than an error you would notice", path, status)
        return []
    return body


def list_deliveries(session, scope, hook_id, limit=30):
    """Recent delivery records for one hook. Read only, corroboration only."""
    base = ("/orgs/%s/hooks/%s/deliveries" % (scope[1:], hook_id)
            if scope.startswith("@")
            else "/repos/%s/hooks/%s/deliveries" % (scope, hook_id))
    status, body = get(session, "%s?per_page=%d" % (base, limit))
    if status != 200 or not isinstance(body, list):
        return None
    return body


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", action="append", default=[],
                    help="owner/name; repeatable")
    ap.add_argument("--org", action="append", default=[],
                    help="organization login; repeatable")
    ap.add_argument("--no-deliveries", action="store_true",
                    help="skip the corroborating read of the delivery log")
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
        stats = summarize(hooks)
        log.info("%d hook(s) on %s, %d inactive", stats["total"], label,
                 stats["inactive"])
        for hook in hooks:
            deliveries = None
            if not args.no_deliveries:
                deliveries = list_deliveries(session, scope, hook.get("id"))
            state, detail = classify(hook, deliveries, now)
            findings.append({"scope": label, "hook_id": hook.get("id"),
                             "state": state, "detail": detail,
                             "last_delivery": newest_delivery(deliveries)})
            if state != "active":
                log.info("%s: %s", state, detail)
                log.info("repair: %s", repair(state, hook, label))
        if stats["inactive"] == 0:
            log.info("active: no hook on %s is switched off", label)

    print(json.dumps({"scopes": scopes, "findings": findings},
                     indent=2, default=str))
    return 1 if any(f["state"] in OFF_STATES for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
