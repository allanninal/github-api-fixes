"""Name the GitHub App permission a 403 was actually asking for.

Read only. GET requests and nothing else. The repair is printed, never
performed, because this script holds a credential that reaches repositories.
"""
import argparse
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_app_permission_diff")

API = "https://api.github.com"
UA = "github-app-permission-diff/1.0"

# Ordered so a comparison is arithmetic. "read" satisfying a "write" requirement
# is the single most common way this error survives a careful look at a settings
# page, and only a ranking catches it.
LEVELS = {"none": 0, "read": 1, "write": 2, "admin": 3}


def parse_accepted(value):
    """Parse x-accepted-github-permissions into (permission, level) pairs. Pure.

    The value is a list of name=level pairs. Endpoints that accept more than one
    route in list more than one pair, and the separator is not consistent across
    every endpoint, so both commas and semicolons are accepted here rather than
    depending on which one a given endpoint used.
    """
    raw = (value or "").strip()
    if not raw:
        return []
    out = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        name, sep, level = chunk.partition("=")
        if not sep or not name.strip():
            continue
        out.append((name.strip(), level.strip().lower()))
    return out


def diff(held, accepted, status=403):
    """Compare what the App holds against what the endpoint asked for. Pure.

    `held` is the permissions map from GET /app, or None when it could not be
    read. `accepted` is the parsed header. Returns (state, detail).

    Where an endpoint lists alternatives, holding one of them can be enough, so
    reporting every unmet pair is a superset. That is the safe direction for a
    diagnostic: it can send you to check a permission you did not need, but it
    will never report one as fine when it is not.
    """
    if status < 400:
        return ("accessible",
                "HTTP %s: the endpoint answered, so there is nothing to diff."
                % (status,))
    if status != 403:
        return ("not-a-permission-error",
                "HTTP %s is not 'Resource not accessible by integration'. A 404 "
                "here is the masked-permission case and a 401 is a dead "
                "credential." % (status,))

    if not accepted:
        return ("endpoint-refuses-apps",
                "403 with no x-accepted-github-permissions header. The endpoint "
                "does not accept an installation token at all, so no permission "
                "you add will open it: use the App equivalent, or a "
                "user-to-server token from the App's OAuth flow.")

    wanted = ", ".join("%s: %s" % (n, l) for n, l in accepted)

    if held is None:
        return ("needed",
                "the endpoint accepts %s. The App's own permission map is not "
                "readable with this credential; read it with GET /app under the "
                "App JWT to see which of those it is missing." % (wanted,))

    missing, low = [], []
    for name, level in accepted:
        have = str(held.get(name) or "none").strip().lower()
        rank = LEVELS.get(have, 0)
        need = LEVELS.get(level, 0)
        if rank == 0:
            missing.append("%s: %s" % (name, level))
        elif rank < need:
            low.append("%s has %s and needs %s" % (name, have, level))

    if not missing and not low:
        return ("sufficient",
                "the App already holds %s, so the permission map is not the "
                "cause. Check that the installation covers this repository and "
                "that the permission upgrade was accepted by this installation."
                % (wanted,))

    if not missing:
        return ("level-too-low",
                "held, but at the wrong level: %s. A permission at 'read' looks "
                "correct on a settings page and is not correct to the endpoint."
                % ("; ".join(low),))

    return ("permission-absent",
            "not held at all: %s.%s" % (", ".join(missing),
                                        (" Also at the wrong level: %s."
                                         % "; ".join(low)) if low else ""))


def get(session, url, **params):
    return session.get(url, params=params, timeout=30)


def held_permissions(session, api):
    """The App's own permission map, or None when the credential cannot read it.

    GET /app needs the App JWT. An installation token gets a 403 here, which is
    a fact about the credential rather than about the App, so None is returned
    and the caller says so out loud.
    """
    r = get(session, api + "/app")
    if r.status_code != 200:
        return None
    return r.json().get("permissions") or {}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", required=True,
                    help="the API path that returns 403, e.g. /repos/acme/api/pulls")
    ap.add_argument("--api", default=API,
                    help="API host, for GitHub Enterprise Server")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (an App installation token, or the App JWT "
                  "if you also want the permission map)")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    path = args.path if args.path.startswith("/") else "/" + args.path
    probe = get(session, args.api + path)
    raw = probe.headers.get("x-accepted-github-permissions")
    log.info("%s -> HTTP %s", path, probe.status_code)
    log.info("x-accepted-github-permissions: %s", raw if raw is not None else "absent")

    accepted = parse_accepted(raw)
    held = held_permissions(session, args.api)
    state, detail = diff(held, accepted, probe.status_code)

    if state == "accessible":
        log.info("%-24s %s", state, detail)
        return 0

    log.warning("%-24s %s", state, detail)
    if held is not None:
        log.warning("  the App holds: %s",
                    ", ".join("%s: %s" % (k, v) for k, v in sorted(held.items()))
                    or "nothing")
    if state in ("permission-absent", "level-too-low"):
        log.warning("  repair: add exactly the permission named above to the App, "
                    "then have every installation owner accept the upgrade. Until "
                    "an installation accepts it, that installation keeps the old "
                    "permission set and keeps returning this same 403.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
