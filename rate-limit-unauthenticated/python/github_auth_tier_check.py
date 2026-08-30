"""Prove which authentication tier your requests are actually in.

Read only. Three GETs: /rate_limit with the token, /rate_limit without it as a
control, and /user. GET /rate_limit does not count against the primary rate
limit, so the check is free in both tiers.

The token is read from the environment and never printed. What comes out is a
fingerprint: the recognised prefix and the length, which is enough to say "this
is a classic personal access token of the usual size" and not enough to use.
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_auth_tier_check")

API = "https://api.github.com"
UA = "github-auth-tier-check/1.0"

ANON_LIMIT = 60

# Prefixes GitHub issues. Recognising one is not proof the token is valid; it is
# only evidence that the variable holds a token rather than a path, a URL or the
# placeholder somebody left in the example file.
PREFIXES = {
    "ghp_": "classic personal access token",
    "github_pat_": "fine-grained personal access token",
    "gho_": "OAuth app user token",
    "ghu_": "GitHub App user-to-server token",
    "ghs_": "GitHub App installation token",
    "ghr_": "GitHub App refresh token",
    "eyJ": "JSON Web Token, signed as a GitHub App",
}

PLACEHOLDERS = ("your", "xxx", "<", ">", "changeme", "replace", "example",
                "placeholder", "dummy", "here", "todo")


def inspect_secret(raw):
    """Describe the environment variable without disclosing it. Pure.

    Returns {"fingerprint", "kind", "problems"}. The distinction that matters
    most is unset against empty: both fail an `if not token` guard, both get
    reported as "not set", and they have different repairs. One is a missing
    export, the other is an export whose value did not survive.
    """
    problems = []
    if raw is None:
        return {"fingerprint": "absent", "kind": None, "problems": ["unset"]}
    if raw == "":
        return {"fingerprint": "empty string", "kind": None, "problems": ["empty"]}

    value = raw.strip()
    if not value:
        return {"fingerprint": "whitespace only", "kind": None, "problems": ["blank"]}
    if value != raw:
        problems.append("padded")

    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        problems.append("quoted")
        value = value[1:-1].strip()

    lowered = value.lower()
    for scheme in ("bearer ", "token "):
        if lowered.startswith(scheme):
            problems.append("scheme-included")
            value = value[len(scheme):].strip()
            lowered = value.lower()
            break

    if any(c.isspace() for c in value):
        problems.append("contains-whitespace")

    kind = None
    for prefix, name in PREFIXES.items():
        if value.startswith(prefix):
            kind = name
            break

    if kind is None:
        problems.append("unknown-prefix")
        # Only look for placeholder wording once the prefix has already failed,
        # so a real token that happens to contain "xxx" is not accused.
        if any(marker in lowered for marker in PLACEHOLDERS):
            problems.append("placeholder")

    prefix_shown = next((p for p in PREFIXES if value.startswith(p)), "unrecognised")
    return {"fingerprint": "%s (%d chars)" % (prefix_shown, len(value)),
            "kind": kind, "problems": problems}


def tier_from_limit(limit):
    """Name the tier a core limit belongs to. Pure.

    Only one boundary here is unambiguous, and it is the one that matters: 60
    against anything larger. The rest is useful colour and is labelled as such,
    because 5,000 is both an authenticated user and the floor for an App
    installation, and the API does not disambiguate them here.
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return ("unknown", "no core limit was reported")

    if limit <= 0:
        return ("unknown", "a core limit of %d is not a tier" % limit)
    if limit <= ANON_LIMIT:
        return ("anonymous",
                "a core limit of %d is the anonymous tier, which is counted per "
                "originating IP address and shared with everything else on it"
                % limit)
    if limit == 5000:
        return ("authenticated",
                "5000 an hour: an authenticated user, an OAuth token, or a "
                "GitHub App installation that has not scaled beyond the floor")
    if limit == 15000:
        return ("enterprise",
                "15000 an hour: a user on GitHub Enterprise Cloud")
    if limit > 5000:
        return ("scaled",
                "%d an hour, above the 5000 floor: a GitHub App installation "
                "whose limit has grown with installed repositories and users"
                % limit)
    return ("authenticated", "%d an hour, which is above the anonymous 60" % limit)


def diagnose(authed_limit, anon_limit, user_status, secret):
    """Combine the local inspection and the two probes into one verdict. Pure.

    "No token" and "a token GitHub refused" both end in anonymous behaviour and
    they are not the same incident, so they do not get the same state.
    """
    secret = secret or {"problems": ["unset"], "fingerprint": "absent"}
    problems = secret.get("problems") or []
    tier, note = tier_from_limit(authed_limit)
    anon_tier, _ = tier_from_limit(anon_limit)

    if any(p in problems for p in ("unset", "empty", "blank")):
        return ("no-token",
                "GITHUB_TOKEN is %s, so every request goes out anonymous at 60 "
                "an hour per IP address. This is not a quota problem and "
                "spending less will not help it."
                % {"unset": "not set", "empty": "set to an empty string",
                   "blank": "whitespace only"}[problems[0]])

    if tier == "anonymous":
        if anon_tier == "anonymous":
            detail = ("the token was sent and GitHub still reports %s. The "
                      "control request without any header reports the same, so "
                      "the header is not arriving." % note)
        else:
            detail = note
        extra = ""
        if "scheme-included" in problems:
            extra = (" The variable itself starts with a scheme word, so the "
                     "header was probably built as \"Bearer Bearer ...\".")
        elif "quoted" in problems:
            extra = (" The variable still has its surrounding quotes, which "
                     "become part of the header value.")
        elif "padded" in problems or "contains-whitespace" in problems:
            extra = (" The variable carries whitespace, which is enough to "
                     "make the header invalid.")
        return ("anonymous", detail + extra)

    if user_status == 401:
        return ("token-rejected",
                "the variable holds %s but GET /user answered 401. The token "
                "is expired, revoked, or the header was removed between here "
                "and GitHub. That is not the same as a missing token."
                % (secret.get("kind") or "an unrecognised value"))

    if user_status == 403:
        return ("blocked",
                "authenticated at %s, but GET /user answered 403. Look at org "
                "SSO authorisation and IP allow lists rather than at the tier."
                % note)

    if user_status == 200:
        return ("authenticated",
                "%s. The anonymous control reports %s, so the header is "
                "arriving." % (note, anon_limit))

    return ("unclear",
            "core limit says %s but GET /user answered %s, so the two probes "
            "do not agree. Treat the limit as the more reliable of the two."
            % (note, user_status))


def get(url, token=None):
    """One GET. Returns (status, body-or-None, headers)."""
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": UA}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        r = requests.get(url, headers=headers, timeout=30)
    except requests.RequestException as exc:
        log.error("%s failed: %s", url, exc)
        return (0, None, {})
    try:
        body = r.json()
    except ValueError:
        body = None
    return (r.status_code, body, dict(r.headers))


def core_limit(body):
    return ((body or {}).get("resources", {}).get("core") or {}).get("limit")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", default="GITHUB_TOKEN",
                    help="environment variable holding the token")
    args = ap.parse_args()

    raw = os.environ.get(args.env)
    secret = inspect_secret(raw)
    log.info("%s: %s%s", args.env, secret["fingerprint"],
             ", " + secret["kind"] if secret["kind"] else "")
    for problem in secret["problems"]:
        log.warning("  variable problem: %s", problem)

    token = (raw or "").strip().strip("\"'").strip()
    for scheme in ("Bearer ", "bearer ", "token ", "Token "):
        if token.startswith(scheme):
            token = token[len(scheme):].strip()
            break

    authed_status, authed_body, authed_headers = get(API + "/rate_limit", token or None)
    anon_status, anon_body, _ = get(API + "/rate_limit")
    user_status, user_body, _ = get(API + "/user", token or None)

    authed = core_limit(authed_body) if authed_status == 200 else None
    anon = core_limit(anon_body) if anon_status == 200 else None
    log.info("with the token:    core limit %s", authed)
    log.info("control, no token: core limit %s", anon)
    log.info("GET /user:         %s%s", user_status,
             " as " + str((user_body or {}).get("login")) if user_status == 200 else "")

    scopes = {k.lower(): v for k, v in authed_headers.items()}.get("x-oauth-scopes")
    if scopes is not None:
        log.info("x-oauth-scopes is present (%r), so this is a classic token or "
                 "an OAuth token rather than a fine-grained one",
                 scopes if scopes else "empty")

    state, detail = diagnose(authed, anon, user_status, secret)
    log.info("%s: %s", state, detail)

    if state != "authenticated":
        log.info("repair: export the token where the process can see it. In a "
                 "container that means passing it in, not exporting it in the "
                 "shell that ran the build.")
        log.info("repair: paste the value only. No surrounding quotes, no "
                 "Bearer prefix, no trailing newline from the file it came out "
                 "of.")
        log.info("repair: assert the tier at startup rather than asserting the "
                 "variable is non-empty. Add this to the top of the process:")
        log.info("  limit = get('%s/rate_limit').json()['resources']['core']"
                 "['limit']", API)
        log.info("  if limit <= %d: raise SystemExit('unauthenticated: refusing "
                 "to run at 60 requests an hour')", ANON_LIMIT)

    print(json.dumps({"state": state, "fingerprint": secret["fingerprint"],
                      "problems": secret["problems"],
                      "authenticated_limit": authed, "anonymous_limit": anon,
                      "user_status": user_status,
                      "tier": tier_from_limit(authed)[0]}, indent=2))
    return 0 if state == "authenticated" else 1


if __name__ == "__main__":
    sys.exit(main())
