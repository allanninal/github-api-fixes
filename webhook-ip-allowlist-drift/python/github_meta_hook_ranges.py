"""Compare GitHub's published webhook source ranges against your allow-list.

Read only, and unauthenticated: GET /meta needs no token, which means the person
who owns the firewall can run this without being issued a GitHub credential.

GET /meta returns the current CIDR ranges GitHub uses, split by purpose. The
hooks array is where webhook deliveries come from. A firewall allow-list copied
out of the documentation once goes stale as those ranges change, and the failure
is partial: only deliveries leaving from a new range are blocked, which reads as
flakiness rather than as a configuration problem.

The allow-list is a file you export, because the one thing this API can never
read is your own network. The accuracy of the answer is the accuracy of that
export.

Usage:

    python3 github_meta_hook_ranges.py --allowlist firewall-github.txt
"""
import argparse
import ipaddress
import json
import logging
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_meta_hook_ranges")

META = "https://api.github.com/meta"
UA = "github-meta-hook-ranges/1.0"

# The array that answers this question, and the ones a list is most often built
# from by mistake. api and web are inbound: they are where you connect to.
HOOKS = "hooks"
OTHER_ARRAYS = ("api", "web", "git", "packages", "actions", "dependabot")
# How much better another array has to score before the finding is "you copied
# the wrong list" rather than "your list has drifted".
WRONG_ARRAY_MARGIN = 0.5


def parse_cidr(text):
    """(version, first_address, last_address) for one entry, or None. Pure.

    Host bits are tolerated, because firewall exports are full of 10.0.0.1/8 and
    refusing them would drop real rules on the floor.
    """
    raw = str(text or "").split("#")[0].strip()
    if not raw:
        return None
    try:
        net = ipaddress.ip_network(raw, strict=False)
    except ValueError:
        return None
    return (net.version, int(net.network_address), int(net.broadcast_address))


def read_allowlist(lines):
    """(ranges, unreadable) from an exported rule list. Pure.

    Unreadable lines are returned rather than skipped. A rule this script cannot
    parse is a hole in the audit, and silently ignoring it would report better
    coverage than the caller actually has.
    """
    ranges, unreadable = [], []
    for line in lines or []:
        text = str(line).split("#")[0].strip()
        if not text:
            continue
        parsed = parse_cidr(text)
        if parsed is None:
            unreadable.append(text)
        else:
            ranges.append(parsed)
    return ranges, unreadable


def size_of(rng):
    """How many addresses a parsed range holds. Pure."""
    return rng[2] - rng[1] + 1


def overlap(a, b):
    """The addresses two ranges share, or None. Pure."""
    if a[0] != b[0]:
        return None
    start, end = max(a[1], b[1]), min(a[2], b[2])
    return (start, end) if start <= end else None


def merge(intervals):
    """Merge overlapping and adjacent intervals. Pure.

    Without this, two allow-list entries that overlap would have their shared
    addresses counted twice and a range could report as more than fully covered.
    """
    out = []
    for start, end in sorted(intervals):
        if out and start <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return [(start, end) for start, end in out]


def covered_addresses(published, allowed):
    """How many addresses of one published range the allow-list permits. Pure."""
    pieces = [piece for piece in (overlap(published, a) for a in allowed) if piece]
    return sum(end - start + 1 for start, end in merge(pieces))


def coverage(published, allowed):
    """(state, fraction) for one published range. Pure.

    Measured over addresses rather than over the text of the CIDRs, so an
    equivalent rewrite of a range is full coverage and a subset is partial with
    the fraction it really permits.
    """
    total = size_of(published)
    covered = covered_addresses(published, allowed)
    if covered <= 0:
        return ("none", 0.0)
    if covered >= total:
        return ("full", 1.0)
    return ("partial", covered / total)


def allows_everything(allowed):
    """Whether a default route makes the allow-list decorative. Pure."""
    for version, start, end in allowed:
        bits = 32 if version == 4 else 128
        if start == 0 and end == (1 << bits) - 1:
            return True
    return False


def audit(published_cidrs, allowed):
    """[(cidr, state, fraction)] for every published range. Pure."""
    rows = []
    for cidr in published_cidrs or []:
        parsed = parse_cidr(cidr)
        if parsed is None:
            rows.append((str(cidr), "unreadable", 0.0))
            continue
        state, fraction = coverage(parsed, allowed)
        rows.append((str(cidr), state, fraction))
    return rows


def uncovered(rows):
    """The published ranges that are not fully covered. Pure."""
    return [cidr for cidr, state, _ in rows if state != "full"]


def array_score(meta, allowed, key):
    """Mean coverage of one /meta array by the allow-list, 0 to 1. Pure."""
    values = (meta or {}).get(key)
    if not isinstance(values, list) or not values:
        return 0.0
    rows = audit(values, allowed)
    return sum(fraction for _, _, fraction in rows) / len(rows)


def best_other_array(meta, allowed):
    """(key, score) for the non-hooks array the allow-list matches best. Pure."""
    best, score = None, 0.0
    for key in OTHER_ARRAYS:
        value = array_score(meta, allowed, key)
        if value > score:
            best, score = key, value
    return (best, score)


def verdict(meta, allowed, unreadable=0):
    """Turn the comparison into a finding. Pure."""
    published = (meta or {}).get(HOOKS)
    if not isinstance(published, list) or not published:
        return ("no-hooks-array",
                "GET /meta did not return a hooks array. Nothing can be "
                "compared until it does.")
    if not allowed:
        return ("no-allowlist",
                "the allow-list is empty, so either nothing is permitted or the "
                "export is wrong. Check the export before reading anything else "
                "here.")
    if allows_everything(allowed):
        return ("allow-all",
                "the allow-list contains a default route, so every published "
                "range is covered and the control is not filtering anything. "
                "This audit will pass forever and mean nothing.")
    rows = audit(published, allowed)
    missing = uncovered(rows)
    hooks_score = array_score(meta, allowed, HOOKS)
    other, other_score = best_other_array(meta, allowed)
    if missing and other and other_score > hooks_score + WRONG_ARRAY_MARGIN:
        return ("wrong-array",
                "the allow-list covers the %s ranges %d%% and the hooks ranges "
                "%d%%. This list was built from the wrong section of GET /meta: "
                "%s is inbound traffic, and webhooks arrive from hooks."
                % (other, round(other_score * 100), round(hooks_score * 100), other))
    if missing:
        return ("drifted",
                "%d of %d published hook ranges are not fully covered by the "
                "allow-list. Partial coverage fails intermittently, which is "
                "why this reads as flakiness rather than as a blocked range."
                % (len(missing), len(rows)))
    if unreadable:
        return ("current-with-gaps",
                "every published hook range is covered, but %d allow-list "
                "entries could not be parsed and were left out of the audit."
                % unreadable)
    return ("current",
            "every published hook range is fully covered by the allow-list.")


def repair(state):
    """The sentence a reader has to act on. Pure."""
    if state in ("drifted", "current-with-gaps"):
        return ("generate the allow-list from GET /meta on a schedule rather "
                "than maintaining it by hand, and alert when the published set "
                "changes so the next change is a pull request instead of an "
                "incident. The current set is printed below in full.")
    if state == "wrong-array":
        return ("rebuild the allow-list from the hooks array. The array in use "
                "is where GitHub serves traffic you connect to, not where "
                "webhook deliveries come from.")
    if state == "allow-all":
        return ("remove the default route or accept that this control does "
                "nothing. Either way, verify X-Hub-Signature-256 on every "
                "request: the signature is what authenticates an event, and an "
                "IP list never was.")
    if state == "no-allowlist":
        return ("export the rules from the device or the infrastructure code "
                "that defines them, one CIDR per line, and run this again.")
    if state == "current":
        return ("nothing today. Put this on a schedule so the answer stays "
                "true, and keep signature verification as the real control.")
    return "nothing."


def fetch_meta(session):
    """The published ranges. Unauthenticated on purpose."""
    r = session.get(META, timeout=30)
    if r.status_code != 200:
        log.error("GET /meta returned %d", r.status_code)
        return None
    try:
        return r.json()
    except ValueError:
        log.error("GET /meta returned a body that is not JSON")
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--allowlist", required=True,
                    help="file of CIDRs your firewall permits, one per line")
    args = ap.parse_args()

    with open(args.allowlist, "r", encoding="utf-8") as fh:
        allowed, unreadable = read_allowlist(fh.readlines())
    for line in unreadable:
        log.warning("allow-list entry not understood, left out of the audit: %s", line)

    session = requests.Session()
    session.headers.update({"Accept": "application/vnd.github+json",
                            "User-Agent": UA})
    meta = fetch_meta(session)
    if meta is None:
        return 2

    published = meta.get(HOOKS) or []
    log.info("GET /meta: %d hooks range(s) published, allow-list holds %d entry/entries",
             len(published), len(allowed))
    rows = audit(published, allowed)
    for cidr, state, fraction in rows:
        log.info("%-22s %-9s %3d%% covered", cidr, state, round(fraction * 100))

    state, detail = verdict(meta, allowed, len(unreadable))
    log.info("%s: %s", state, detail)
    log.info("repair: %s", repair(state))
    if state in ("drifted", "wrong-array", "current-with-gaps"):
        log.info("the published hooks ranges, in full:")
        for cidr in published:
            log.info("  %s", cidr)

    print(json.dumps({
        "published_hooks_ranges": published,
        "allowlist_entries": len(allowed),
        "allowlist_unreadable": unreadable,
        "coverage": [{"cidr": c, "state": s, "fraction": round(f, 4)}
                     for c, s, f in rows],
        "not_fully_covered": uncovered(rows),
        "hooks_score": round(array_score(meta, allowed, HOOKS), 4),
        "best_other_array": best_other_array(meta, allowed)[0],
        "state": state,
        "detail": detail,
        "repair": repair(state),
    }, indent=2, default=str))
    return 1 if state in ("drifted", "wrong-array", "allow-all", "no-allowlist") else 0


if __name__ == "__main__":
    sys.exit(main())
