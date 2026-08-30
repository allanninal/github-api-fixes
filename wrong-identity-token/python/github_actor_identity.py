"""Say whether the credential doing your automation is a person.

Read only. Three GETs at most, all of them reads of things the credential can
already see: its own profile, the organizations it reaches, and optionally the
recent commits of one repository. Nothing is created, renamed or revoked, and
the repair is printed.

This script deliberately reads the response body rather than the response
headers. Scopes and expiry are different questions with different notes; the
question here is whose access this credential borrows, and the answer decides
what happens on somebody's last day.
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_actor_identity")

API = "https://api.github.com"
UA = "github-actor-identity/1.0"

# Login fragments that suggest an account was created for a machine. Matched as
# whole tokens after splitting on separators, so "cindy" is not read as "ci"
# and "abbot" is not read as "bot".
MACHINE_HINTS = {
    "bot", "bots", "ci", "cd", "svc", "service", "serviceaccount", "machine",
    "automation", "deploy", "deployer", "robot", "jenkins", "buildbot",
    "integration", "noreply", "actions", "runner",
}

# What stays coupled to a human being, per verdict. Phrased as the thing that
# happens rather than as the principle, because "use a service account" has
# been said in every one of these code reviews already.
COUPLINGS = {
    "personal-account": [
        "commits, comments and reviews are attributed to this person in the "
        "history, permanently",
        "deprovisioning the account kills every token on it, without warning "
        "and without naming what breaks",
        "removing them from an organization removes the automation's access "
        "to it on the same afternoon",
        "an expired SAML single sign-on session stops the token mid-run",
        "their two-factor changes, device losses and password resets are all "
        "in the failure path",
    ],
    "mixed-signals": [
        "the login is machine shaped and the profile is not, which usually "
        "means a person's account was renamed or a shared login sits on "
        "somebody's mailbox",
        "whoever controls that mailbox controls the credential",
    ],
    "machine-account": [
        "it still consumes a seat and still needs two-factor authentication",
        "it still needs SAML single sign-on authorization per organization",
        "its password and recovery codes live somewhere, and that somewhere "
        "needs an owner who is not one person",
    ],
    "unclassified-user": [
        "the account is a User with nothing that says who owns it, which is "
        "the state that produces an unattributable credential",
    ],
}


def identity(body):
    """Normalise the GET /user body into the fields that matter. Pure.

    Returns None when the body is not a profile at all, which is what an
    installation access token produces: it has no user, so there is nothing to
    read and that is the healthy answer.
    """
    if not isinstance(body, dict) or not body.get("login"):
        return None
    return {
        "login": str(body.get("login")),
        "type": str(body.get("type") or "Unknown"),
        "name": body.get("name") or None,
    }


def looks_like_a_person_name(value):
    """Whether a profile name reads as a personal name. Pure.

    Two or more capitalised alphabetic words. Wrong for mononyms and for a
    great many naming cultures, which is why it is one signal among several
    and never the verdict on its own.
    """
    parts = [p for p in str(value or "").replace(".", " ").split() if p]
    if len(parts) < 2:
        return False
    return all(p[:1].isupper() and p.isalpha() for p in parts)


def machine_shaped(login, declared=()):
    """Whether a login was plainly created for a machine. Pure.

    A declared list from your own inventory wins over the naming heuristic,
    because an organization that calls its machine account "hermes" knows
    something this script cannot.
    """
    name = str(login or "").lower()
    if name in {str(d).lower() for d in declared or ()}:
        return True
    if name.endswith("[bot]"):
        return True
    tokens = set()
    current = ""
    for ch in name:
        if ch.isalnum():
            current += ch
        else:
            tokens.add(current)
            current = ""
    tokens.add(current)
    return bool(tokens & MACHINE_HINTS)


def human_signals(body):
    """Evidence that this account belongs to a person. Pure.

    Each entry is a sentence rather than a score, so a reader can disagree with
    one of them without discarding the report. The email address is counted and
    never quoted: its presence is the signal, its value is somebody's inbox.
    """
    found = []
    if not isinstance(body, dict):
        return found
    if looks_like_a_person_name(body.get("name")):
        found.append("a personal name is set: %s" % body.get("name"))
    if body.get("bio"):
        found.append("a bio is set")
    if body.get("hireable"):
        found.append("hireable is set, which no service account needs")
    if body.get("email"):
        found.append("a public email address is set")
    if body.get("twitter_username"):
        found.append("a social handle is set")
    followers = body.get("followers")
    if isinstance(followers, int) and followers >= 5:
        found.append("%d followers" % followers)
    return found


def classify(ident, signals, machine):
    """Sort a credential's identity into one of six states. Pure."""
    if ident is None:
        return ("identity-unreadable",
                "the credential could not answer GET /user, which is what an "
                "installation access token does: it has no user behind it. "
                "That is the healthy answer to the question this script asks, "
                "and it is also why some endpoints refuse such tokens.")
    if ident["type"] == "Bot" or ident["login"].lower().endswith("[bot]"):
        return ("app-installation",
                "%s is a Bot identity, so the work is done by a GitHub App "
                "installation rather than by a person. Nothing here is "
                "coupled to anyone's employment." % ident["login"])
    if signals and machine:
        return ("mixed-signals",
                "%s is named like a machine account and carries %d human "
                "signal(s): %s. Usually a person's account renamed, or a "
                "shared login on one person's mailbox."
                % (ident["login"], len(signals), "; ".join(signals)))
    if signals:
        return ("personal-account",
                "%s is a %s with %d human signal(s): %s. The automation is "
                "running as a person."
                % (ident["login"], ident["type"], len(signals),
                   "; ".join(signals)))
    if machine:
        return ("machine-account",
                "%s is named like a machine account and carries no human "
                "signals. Better than a colleague's token, and still an "
                "account with a seat, a password and an SSO state."
                % ident["login"])
    return ("unclassified-user",
            "%s is a %s with no human signals and no machine naming, so this "
            "script will not guess. Somebody owns it; find out who before the "
            "question is urgent." % (ident["login"], ident["type"]))


def couplings(state):
    """What remains tied to a human being, given a verdict. Pure."""
    return list(COUPLINGS.get(state, []))


def attributed(commits, login):
    """How many of these commits are attributed to a login. Pure.

    author is null for a commit whose email matches no account, which is common
    and is not the same as being attributed to somebody else, so it is counted
    separately rather than folded into either side.
    """
    total = 0
    mine = 0
    unlinked = 0
    for commit in commits or []:
        if not isinstance(commit, dict):
            continue
        total += 1
        author = commit.get("author")
        if not isinstance(author, dict) or not author.get("login"):
            unlinked += 1
        elif str(author["login"]).lower() == str(login or "").lower():
            mine += 1
    return {"total": total, "attributed": mine, "unlinked": unlinked}


def get(session, path):
    """One GET. Returns (status, json-or-None)."""
    r = session.get(API + path, timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", help="OWNER/REPO, to count how much of its "
                                   "recent history this identity signed")
    ap.add_argument("--machine-logins", default="",
                    help="comma-separated logins your inventory already calls "
                         "machine accounts")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN to the credential your automation uses. "
                  "An anonymous request has no identity to report")
        return 2
    declared = [d.strip() for d in args.machine_logins.split(",") if d.strip()]

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    status, body = get(session, "/user")
    ident = identity(body) if status == 200 else None
    if ident:
        log.info("login=%s type=%s", ident["login"], ident["type"])
    else:
        log.info("GET /user returned %d with no profile in it", status)

    signals = human_signals(body if status == 200 else None)
    machine = machine_shaped(ident["login"], declared) if ident else False
    state, detail = classify(ident, signals, machine)
    log.info("%s: %s", state, detail)

    if ident and state != "app-installation":
        org_status, orgs = get(session, "/user/orgs")
        if org_status == 200 and isinstance(orgs, list):
            names = [o.get("login") for o in orgs if isinstance(o, dict)]
            log.info("this identity reaches %d organization(s) through one "
                     "person's membership: %s", len(names),
                     ", ".join(n for n in names if n) or "none listed")
        else:
            log.info("GET /user/orgs returned %d, so the organizations this "
                     "identity borrows could not be listed", org_status)

    if args.repo and ident:
        commit_status, commits = get(
            session, "/repos/%s/commits?per_page=100" % args.repo)
        if commit_status == 200:
            counts = attributed(commits, ident["login"])
            log.info("attribution: %d of the last %d commits in %s are "
                     "attributed to %s (%d are linked to no account at all)",
                     counts["attributed"], counts["total"], args.repo,
                     ident["login"], counts["unlinked"])
        else:
            log.info("GET commits for %s returned %d", args.repo, commit_status)

    for line in couplings(state):
        log.info("coupled: %s", line)

    if state in ("personal-account", "mixed-signals", "unclassified-user"):
        log.info("repair: install a GitHub App owned by the organization and "
                 "run the automation as its installation. The identity becomes "
                 "my-app[bot] and no leaver process touches it.")
        log.info("repair: if an App is genuinely not possible, create a "
                 "dedicated machine account, document its owner, and put its "
                 "credentials in the team's secret manager rather than on one "
                 "person's laptop.")

    print(json.dumps({"login": ident["login"] if ident else None,
                      "type": ident["type"] if ident else None,
                      "human_signals": signals, "machine_shaped": machine,
                      "state": state}, indent=2))
    return 1 if state in ("personal-account", "mixed-signals") else 0


if __name__ == "__main__":
    sys.exit(main())
