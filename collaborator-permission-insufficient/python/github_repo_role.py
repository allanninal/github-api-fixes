"""Read an account's role on one repository instead of provoking a 403.

Read only. GET requests and nothing else. This script is about writes being
refused and it never attempts one: the role is readable in advance on an
ordinary repository read, the minimum role for each action is documented, and
the comparison between them is arithmetic done locally. A tool that proved
"this account cannot merge" by attempting a merge would, in the case where it
was wrong, have merged something.

The point of the note: an OAuth scope bounds what a token may do on the
account's behalf and cannot grant the account access it does not have. A token
carrying `repo` held by an account whose role on that repository is read is
powerless there, and widening the token cannot change that.

What this can and cannot see: the permissions object is the effective role,
already accounting for a direct collaborator grant, every team the account is
in and the organization base permission. It never reports which of those
produced the role. Naming the source needs organization reads a repository
token does not have, so this script reports the effect and says so.

Environment:

    GITHUB_TOKEN    a token with read access to the repository
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_repo_role")

API = "https://api.github.com"
UA = "github-repo-role/1.0"

# Weakest first. Every role implies the ones below it, which is why resolving
# the permissions object is "highest flag that is true" and nothing more.
ROLES = ("none", "read", "triage", "write", "maintain", "admin")

# The booleans GitHub returns, paired with the role each one names. Ordered
# strongest first so the first hit is the answer.
PERMISSION_FLAGS = (
    ("admin", "admin"),
    ("maintain", "maintain"),
    ("push", "write"),
    ("triage", "triage"),
    ("pull", "read"),
)

# The legacy `permission` string on the collaborator endpoint has four values
# and rounds two roles into their neighbours: a maintainer reads as write, a
# triager reads as read. role_name carries the real one.
LEGACY_ROUNDING = {
    "admin": "admin",
    "write": "write",
    "read": "read",
    "none": "none",
}

# Minimum role for actions people actually get refused on. Kept short and
# documented rather than exhaustive: the value is in the four rows that
# surprise people, not in a transcription of the whole roles table.
ACTION_MINIMUM = {
    "read-code": "read",
    "clone": "read",
    "open-issue": "read",
    "comment": "read",
    "label-issue": "triage",
    "close-issue": "triage",
    "assign-issue": "triage",
    "request-review": "triage",
    "push-branch": "write",
    "merge-pull-request": "write",
    "create-release": "write",
    "dismiss-review": "write",
    "manage-repository-settings": "maintain",
    "manage-webhooks": "admin",
    "add-collaborator": "admin",
    "change-visibility": "admin",
}

# Longest prefixes first so a future prefix extending an existing one is not
# swallowed by its shorter neighbour.
TOKEN_PREFIXES = (
    ("github_pat_", "fine-grained PAT"),
    ("ghp_", "classic PAT"),
    ("gho_", "OAuth user token"),
    ("ghu_", "App user-to-server token"),
    ("ghs_", "App installation token"),
    ("ghr_", "App refresh token"),
)

# The widest a classic token gets on repositories. Holding it and still being
# refused is the shape this note exists to interrupt.
WIDEST_CLASSIC_REPO_SCOPE = "repo"


def read_cost(with_user=False):
    """Requests this run will spend against the core quota. Pure."""
    return 3 if with_user else 2


def token_kind(token):
    """Name the credential from its prefix. Pure; nothing leaves the machine."""
    value = (token or "").strip()
    for prefix, name in TOKEN_PREFIXES:
        if value.startswith(prefix):
            return name
    return "unknown"


def scope_list(header_value):
    """Read x-oauth-scopes into a list, keeping absent and empty apart. Pure.

    A classic token with nothing ticked sends the header with an empty value; a
    fine-grained token or an App token does not send it at all. Collapsing both
    to [] would lose the signal that decides whether "widen the scopes" is even
    a sentence that applies.
    """
    if header_value is None:
        return None
    return [s.strip() for s in header_value.split(",") if s.strip()]


def role_rank(role):
    """Position in the hierarchy, or -1 for something unrecognised. Pure."""
    try:
        return ROLES.index(str(role or "none").strip().lower())
    except ValueError:
        return -1


def role_from_permissions(permissions):
    """The role a permissions object describes. Pure.

    Highest true flag wins. An empty or missing object is "unreported" rather
    than "none": an unauthenticated read returns no permissions object at all,
    and reporting that as no access would be a different and wrong finding.
    """
    if not isinstance(permissions, dict) or not permissions:
        return "unreported"
    for flag, role in PERMISSION_FLAGS:
        if permissions.get(flag) is True:
            return role
    return "none"


def role_from_collaborator(payload):
    """Resolve the collaborator permission endpoint. Pure.

    Returns (role, exact, note). role_name is preferred because the legacy
    `permission` field rounds maintain down to write and triage down to read.
    An unrecognised role_name is a custom organization role: named, not priced,
    because the API does not publish what a custom role can do.
    """
    if not isinstance(payload, dict):
        return ("unreported", False, "no collaborator permission payload was read.")
    name = str(payload.get("role_name") or "").strip().lower()
    legacy = str(payload.get("permission") or "").strip().lower()
    if name and role_rank(name) >= 0:
        return (name, True, "role_name reported the exact role.")
    if name:
        return ("custom:" + name, False,
                "role_name is '%s', a custom organization role. Its abilities "
                "are defined by the organization and are not published through "
                "this API, so nothing here prices it." % name)
    if legacy in LEGACY_ROUNDING:
        return (LEGACY_ROUNDING[legacy], False,
                "only the legacy permission field was present. It rounds "
                "maintain to write and triage to read, so a maintainer and a "
                "triager are both misreported by it.")
    return ("unreported", False, "neither role_name nor permission was present.")


def can(role, action):
    """Does this role reach the documented minimum for this action. Pure."""
    needed = ACTION_MINIMUM.get(str(action or "").strip().lower())
    if needed is None:
        return None
    held = role_rank(role)
    if held < 0:
        return None
    return held >= role_rank(needed)


def deficit(role, action):
    """How many roles short this account is for this action. Pure.

    0 means it is sufficient. None means the question could not be asked, which
    happens for a custom role or an unknown action and is not the same as no.
    """
    needed = ACTION_MINIMUM.get(str(action or "").strip().lower())
    held = role_rank(role)
    if needed is None or held < 0:
        return None
    return max(0, role_rank(needed) - held)


def blocked_actions(role):
    """Every documented action this role cannot perform. Pure."""
    held = role_rank(role)
    if held < 0:
        return []
    return sorted(a for a, need in ACTION_MINIMUM.items() if held < role_rank(need))


def scopes_are_the_ceiling(role, scopes, kind, action):
    """Is widening the credential capable of changing the answer. Pure.

    Returns (state, detail). This is the question the reader is actually asking
    and it deserves an explicit answer rather than an inference, because the
    default guess is yes and the default guess is wrong.
    """
    short = deficit(role, action)
    if short is None or short == 0:
        return ("not-the-question",
                "the role is sufficient for this action, so the credential is "
                "the next thing to look at rather than the first.")
    if scopes is None:
        return ("no-scopes-to-widen",
                "this credential is a %s and carries no OAuth scopes at all, so "
                "there is nothing to widen. Its per-resource permissions are a "
                "separate gate and neither gate raises a repository role."
                % kind)
    if WIDEST_CLASSIC_REPO_SCOPE in scopes:
        return ("scopes-are-not-the-ceiling",
                "the token carries '%s', which is as wide as a classic token "
                "gets on repositories. Reminting it wider cannot change this "
                "answer." % WIDEST_CLASSIC_REPO_SCOPE)
    return ("two-gates-open",
            "the token holds %s and not '%s', so the scope is worth fixing too. "
            "Fixing it alone will not help: the role is short as well, and both "
            "gates have to open."
            % (", ".join(scopes) or "no scopes at all", WIDEST_CLASSIC_REPO_SCOPE))


def verdict(role, action):
    """Classify one account's role against one action. Pure. (state, detail)."""
    if str(role).startswith("custom:"):
        return ("custom-role",
                "the role is a custom organization role, which this script "
                "names and does not price. Ask an organization owner what it "
                "grants, or compare against the base role it was built from.")
    if role == "unreported":
        return ("role-unreported",
                "no permissions object came back. An unauthenticated read never "
                "carries one, so authenticate before reading anything into this.")
    if role == "none":
        return ("no-access",
                "the account has no role on this repository at all. Reads of a "
                "private repository will 404 rather than 403, which is a "
                "different symptom with the same cause.")
    short = deficit(role, action)
    if short is None:
        return ("action-unknown",
                "no documented minimum role is held here for that action, so "
                "the role is reported and the comparison is left to you.")
    if short == 0:
        return ("role-sufficient",
                "this account holds '%s' and %s needs '%s', so the role is not "
                "what refused the call."
                % (role, action, ACTION_MINIMUM[action]))
    return ("role-insufficient",
            "this account holds '%s' and %s needs '%s', which is %d role(s) "
            "higher." % (role, action, ACTION_MINIMUM[action], short))


def repair(state, role, action, subject="this account"):
    """The sentence a reader has to act on. Pure."""
    if state == "role-insufficient":
        return ("have somebody with admin raise %s to '%s' on this repository, "
                "or add it to a team that has it. The permissions object "
                "reports the effective role and never its source, so the grant "
                "may need making in a team or in the org's base permission."
                % (subject, ACTION_MINIMUM[action]))
    if state == "no-access":
        return ("grant %s a role on the repository. Until then the repository "
                "is invisible rather than forbidden if it is private." % subject)
    if state == "role-sufficient":
        return ("nothing on the role. Read the refusal's headers next: a "
                "classic token names what it accepts in x-accepted-oauth-scopes "
                "and a fine-grained one names nothing at all.")
    if state == "custom-role":
        return ("ask an organization owner which base role this custom role was "
                "built from, then compare that against the action.")
    if state == "role-unreported":
        return ("authenticate the read. The permissions object only arrives on "
                "an authenticated request.")
    return ("name an action with --action to turn the role into a verdict. "
            "The role itself is already reported above.")


def get(session, path):
    """One GET. Returns the response object."""
    r = session.get(API + path, timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, malformed "
                         "or revoked")
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo", help="owner/name of the repository")
    ap.add_argument("--action", default="merge-pull-request",
                    help="the action being refused, e.g. merge-pull-request, "
                         "label-issue, push-branch")
    ap.add_argument("--user",
                    help="report this account's role instead of the token's own")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2
    if "/" not in args.repo:
        log.error("repo should be owner/name")
        return 2

    log.info("read cost: %d request(s) against the core hourly quota",
             read_cost(bool(args.user)))

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })

    kind = token_kind(token)
    me = get(session, "/user")
    scopes = scope_list(me.headers.get("x-oauth-scopes"))
    log.info("token: %s, scopes=%s", kind,
             "none" if scopes is None else (", ".join(scopes) or "empty"))

    repo_response = get(session, "/repos/" + args.repo)
    if repo_response.status_code != 200:
        log.error("%s: HTTP %s reading the repository. A 404 here is its own "
                  "note; this one starts from a repository you can read.",
                  args.repo, repo_response.status_code)
        return 2
    repo = repo_response.json()
    permissions = repo.get("permissions") or {}
    role = role_from_permissions(permissions)
    subject = "this account"
    note = None

    if args.user:
        collab = get(session, "/repos/%s/collaborators/%s/permission"
                     % (args.repo, args.user))
        if collab.status_code == 200:
            role, _exact, note = role_from_collaborator(collab.json())
            subject = args.user
        else:
            log.warning("collaborator permission read returned HTTP %s; "
                        "reporting the token's own role instead",
                        collab.status_code)

    log.info("%s: permissions=%s", args.repo, json.dumps(permissions))
    log.info("role: %s", role)
    if note:
        log.info("role source: %s", note)

    state, detail = verdict(role, args.action)
    log.info("%s: %s", state, detail)
    ceiling_state, ceiling_detail = scopes_are_the_ceiling(
        role, scopes, kind, args.action)
    log.info("%s: %s", ceiling_state, ceiling_detail)
    log.info("repair: %s", repair(state, role, args.action, subject))

    blocked = blocked_actions(role)
    if blocked:
        log.info("also blocked at this role: %s", ", ".join(blocked))

    print(json.dumps({
        "repository": args.repo,
        "subject": subject,
        "token_kind": kind,
        "scopes": scopes,
        "permissions": permissions,
        "role": role,
        "action": args.action,
        "minimum_role": ACTION_MINIMUM.get(args.action),
        "roles_short": deficit(role, args.action),
        "state": state,
        "detail": detail,
        "credential_state": ceiling_state,
        "credential_detail": ceiling_detail,
        "blocked_actions": blocked,
        "repair": repair(state, role, args.action, subject),
    }, indent=2, default=str))
    return 1 if state in ("role-insufficient", "no-access") else 0


if __name__ == "__main__":
    sys.exit(main())
