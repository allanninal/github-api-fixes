"""Say which stored credentials will be reaped for disuse before they are needed.

Read only, and free: one GET /rate_limit per credential, which consumes no
quota and requires no scope. That call is also the mitigation, because it
counts as a use, so running this on a schedule is both the monitoring and the
repair. Nothing here mints, rotates or revokes anything.

GitHub removes classic personal access tokens that have gone a year without
being used. That class of credential carries no expiry and emits no header, so
the clock cannot come from the API. It comes from the manifest: how often each
credential is actually exercised by the job that owns it.

Manifest format, a JSON list:

    [{"env": "GITHUB_TOKEN", "label": "nightly sync",
      "exercised_every_days": 1},
     {"env": "DR_RESTORE_TOKEN", "label": "annual restore drill",
      "exercised_every_days": 365}]
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_token_dormancy")

API = "https://api.github.com"
UA = "github-token-dormancy/1.0 (+https://example.com/contact)"

# The published dormancy window, and the margin below which one skipped run
# loses the race. Both are arguments rather than constants at the call sites
# so a stricter shop can tighten them without editing the logic.
WINDOW_DAYS = 365
TIGHT_DAYS = 60


def token_class(value):
    """Name the credential class from its prefix. Pure. Never returns the value."""
    if value is None or not str(value).strip():
        return "absent"
    text = str(value).strip()
    if text.startswith("github_pat_"):
        return "fine-grained"
    if text.startswith("ghp_"):
        return "classic"
    if text.startswith("ghs_"):
        return "installation"
    if text.startswith("gho_") or text.startswith("ghu_"):
        return "oauth"
    if len(text) == 40 and all(c in "0123456789abcdef" for c in text.lower()):
        return "classic"
    return "unknown"


def reap_exposure(kind, expires_header):
    """Decide whether a credential is even in the class the reaper can take. Pure.

    The header check comes first and wins. A credential that reports an expiry
    has a date, and a date is a different clock with a different note attached
    to it; dormancy cannot reach it.
    """
    if expires_header:
        return ("not-reapable-expiring",
                "this credential reports an expiry, so it dies on a date "
                "rather than from disuse. The countdown on that date is a "
                "different check.")
    if kind == "classic":
        return ("reapable",
                "a classic token with no expiry reported. This is the only "
                "class GitHub removes for disuse, and it emits no header to "
                "warn you.")
    if kind == "fine-grained":
        return ("not-reapable-fine-grained",
                "fine-grained tokens carry an expiry by default, so they are "
                "governed by a date even when this request did not show one.")
    if kind == "installation":
        return ("not-reapable-short-lived",
                "an installation access token lives about an hour. It is "
                "minted per run and dormancy is meaningless for it.")
    if kind == "oauth":
        return ("not-reapable-oauth",
                "an OAuth user token dies when somebody revokes the "
                "authorization, which is a decision rather than a clock.")
    return ("unknown-class",
            "the credential does not match a known prefix, so its class "
            "cannot be named from its text. Treat it as reapable until "
            "somebody confirms otherwise.")


def margin_days(interval_days, window_days=WINDOW_DAYS):
    """Days of headroom between one use and the reaping window. Pure.

    None when the cadence is unknown, because guessing here produces a
    confident wrong answer about a credential nobody is watching.
    """
    try:
        interval = float(interval_days)
    except (TypeError, ValueError):
        return None
    return window_days - interval


def dormancy_state(probe_status, exposure, interval_days,
                   window_days=WINDOW_DAYS, tight_days=TIGHT_DAYS):
    """Turn a probe result and an exercise cadence into a finding. Pure."""
    if probe_status == 401:
        return ("already-gone",
                "the credential is refused. For this class there is nothing "
                "to un-revoke: mint a replacement and record what it is for.")
    if probe_status is None or probe_status >= 400:
        return ("unreachable",
                "the probe did not come back cleanly, so nothing can be said "
                "about the credential yet. Fix the probe first.")
    if exposure != "reapable" and exposure != "unknown-class":
        return ("not-reapable",
                "alive, and not in the class that gets reaped for disuse.")
    margin = margin_days(interval_days, window_days)
    if margin is None:
        return ("cadence-unknown",
                "alive, and reapable, but the manifest does not say how often "
                "anything exercises it. That is the number this check needs.")
    if margin <= 0:
        return ("reap-race-lost",
                "alive today, and nothing exercises it inside the window. "
                "This credential will be removed before it is next needed.")
    if margin < tight_days:
        return ("reap-race-tight",
                "alive, with less headroom than one skipped run. A paused "
                "pipeline or a quiet quarter loses this race.")
    return ("covered",
            "alive, and exercised often enough that the job itself keeps the "
            "credential from going dormant.")


def probe_interval(interval_days, window_days=WINDOW_DAYS):
    """Recommend a keep-alive cadence in days. Pure.

    At most thirty days, because a monthly probe leaves eleven months of
    margin and costs twelve free requests a year. Never slower than the job it
    protects, because a probe slower than the job adds nothing to it.
    """
    try:
        interval = float(interval_days)
    except (TypeError, ValueError):
        interval = float(window_days)
    return int(max(1, min(30.0, interval)))


def keepalive_cron(days):
    """A crontab line for a keep-alive at the given cadence. Pure."""
    if days <= 1:
        return "0 6 * * *"
    if days <= 7:
        return "0 6 * * 1"
    return "0 6 1 * *"


def probe(token):
    """One free GET. Returns (status, expiry header or None)."""
    response = requests.get(API + "/rate_limit", timeout=30, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })
    headers = {k.lower(): v for k, v in response.headers.items()}
    return response.status_code, headers.get("github-authentication-token-expiration")


def load_manifest(path):
    """Read the manifest, or build a one-entry one from the environment."""
    if path:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    return [{"env": "GITHUB_TOKEN", "label": "the credential in GITHUB_TOKEN",
             "exercised_every_days": None}]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest",
                    help="JSON list of {env, label, exercised_every_days}")
    ap.add_argument("--window-days", type=int, default=WINDOW_DAYS,
                    help="the dormancy window to measure against")
    ap.add_argument("--tight-days", type=int, default=TIGHT_DAYS,
                    help="margin below which one skipped run loses the race")
    args = ap.parse_args()

    entries = load_manifest(args.manifest)
    findings = []
    for entry in entries:
        name = entry.get("env", "")
        label = entry.get("label", name)
        cadence = entry.get("exercised_every_days")
        token = os.environ.get(name)
        if not token:
            log.warning("%-20s %-24s no value in the environment", name, label)
            findings.append({"env": name, "state": "not-set"})
            continue

        kind = token_class(token)
        status, expires = probe(token)
        exposure, exposure_detail = reap_exposure(kind, expires)
        state, detail = dormancy_state(status, exposure, cadence,
                                       args.window_days, args.tight_days)
        margin = margin_days(cadence, args.window_days)
        log.info("%-20s %-24s %-16s margin %s", name, label, state,
                 "%dd" % margin if margin is not None else "unknown")
        log.info("    class %s: %s", kind, exposure_detail)
        log.info("    %s", detail)

        if state in ("reap-race-lost", "reap-race-tight", "cadence-unknown"):
            every = probe_interval(cadence, args.window_days)
            log.info("    repair: probe this credential every %d days. "
                     "GET /rate_limit costs no quota, needs no scope, and "
                     "counts as a use, so the probe is the fix.", every)
            log.info("    crontab: %s", keepalive_cron(every))
            log.info("    repair: schedule it separately from the job that "
                     "owns the credential. A check inside an annual job runs "
                     "annually, which is the interval that caused this.")
        if state == "already-gone":
            log.info("    repair: mint a replacement, then record its purpose "
                     "and owner somewhere the next drill will find them.")

        findings.append({"env": name, "label": label, "class": kind,
                         "exposure": exposure, "status": status,
                         "margin_days": margin, "state": state})

    print(json.dumps(findings, indent=2))
    bad = {"reap-race-lost", "reap-race-tight", "already-gone"}
    return 1 if any(f.get("state") in bad for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
