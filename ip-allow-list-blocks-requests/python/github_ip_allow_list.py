"""Say whether a 403 came from an organization IP allow list.

Read only. Two GETs, plus one optional GraphQL query, which is a read that
happens to travel over the same verb a write would. Nothing is added to any
allow list: an allow-list entry is organization state, so the script compares
what is there against the address GitHub saw and prints the request for
somebody with organization admin to make.

The point of the note: this refusal is a check on the source address, not on
the credential. The identical token succeeds from a laptop and is refused from
an ephemeral CI runner, which is why every experiment that varies the token
comes back clean.

What this can and cannot see: the refusal body names the address GitHub applied
the rule to, which is stronger evidence than any echo service. The list itself
is organization state and needs admin:org-class access; without it this script
reports the effect and says the rule is unreadable rather than pretending an
unreadable list is an empty one.

Environment:

    GITHUB_TOKEN    the same read-only token the failing job holds
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_ip_allow_list")

API = "https://api.github.com"
UA = "github-ip-allow-list/1.0"

# Sentences the four other causes of a 403 put in the body. Matched in
# lowercase and only as corroboration: the decisive test for an allow-list
# refusal is that the body contains an IP address, which none of the others do,
# so a reworded sentence cannot silently break the classification.
QUOTA_MARKERS = ("api rate limit exceeded",)
SECONDARY_MARKERS = ("secondary rate limit",)
USER_AGENT_MARKERS = ("user-agent", "user agent")
ALLOW_LIST_MARKERS = ("ip allow list", "not permitted to access this resource")

# Read for ipAllowListEnabledSetting and the entries. One query, one point, and
# it is refused before it is sent if the document stops being a read.
ALLOW_LIST_QUERY = """
query($login: String!) {
  organization(login: $login) {
    ipAllowListEnabledSetting
    ipAllowListForInstalledAppsEnabledSetting
    ipAllowListEntries(first: 100) {
      nodes { allowListValue isActive name }
    }
  }
}
"""

# Which list judges which credential. An App-managed allow list contributes the
# App's ranges for installation tokens; a user-to-server token acts for a
# person and is judged against the organization's own list regardless.
TOKEN_PREFIXES = (
    ("github_pat_", "fine-grained PAT"),
    ("ghp_", "classic PAT"),
    ("gho_", "OAuth user token"),
    ("ghu_", "App user-to-server token"),
    ("ghs_", "App installation token"),
    ("ghr_", "App refresh token"),
)

APP_MANAGED_APPLIES = ("App installation token",)


def read_cost(with_allow_list=False):
    """(REST requests, GraphQL points) this run will spend. Pure.

    Printed before anything is spent. The GraphQL half is counted separately
    because it comes out of a different budget and a reader deciding whether to
    pass --org-allow-list is deciding about that budget, not this one.
    """
    return (2, 1 if with_allow_list else 0)


def token_kind(token):
    """Name the credential from its prefix. Pure; nothing leaves the machine."""
    value = (token or "").strip()
    for prefix, name in TOKEN_PREFIXES:
        if value.startswith(prefix):
            return name
    return "unknown"


def list_that_applies(kind):
    """Which allow list judges this credential. Pure. (which, detail).

    The case that confuses people: an App with its own allow list keeps its
    background sync working while the same App's interactive calls are refused,
    because those calls carry a user-to-server token and are judged against the
    organization's list.
    """
    if kind in APP_MANAGED_APPLIES:
        return ("org-list-plus-app-managed",
                "an installation token is judged against the organization's "
                "list, and where the organization has enabled the App-managed "
                "setting the App's own ranges are contributed to it "
                "automatically.")
    if kind == "App user-to-server token":
        return ("org-list-only",
                "a user-to-server token acts for a person, so it is judged "
                "against the organization's own list even when the App's "
                "ranges are allowed. An App whose background sync works and "
                "whose interactive calls do not is this exact case.")
    return ("org-list-only",
            "this credential carries no App identity, so only the "
            "organization's own allow list applies to it.")


def looks_like_ipv4(text):
    """Four dot-separated numbers in 0..255. Pure. No regular expression.

    Written as arithmetic rather than a pattern because the input is a
    human-readable sentence and the parts that matter -- a trailing full stop,
    a leading bracket -- are easier to strip than to describe in a pattern.
    """
    parts = str(text or "").split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit() or len(part) > 3:
            return False
        if int(part) > 255:
            return False
    return True


def looks_like_ipv6(text):
    """A rough IPv6 test: colons, hex groups only. Pure.

    Rough on purpose. The script never does arithmetic on an IPv6 address; it
    only needs to recognise one well enough to report it and to say that the
    containment check was not run.
    """
    value = str(text or "")
    if value.count(":") < 2:
        return False
    for group in value.split(":"):
        if group == "":
            continue
        if len(group) > 4:
            return False
        for ch in group.lower():
            if ch not in "0123456789abcdef":
                return False
    return True


def address_in_message(message):
    """The address GitHub says it saw, or None. Pure.

    Tokenised rather than matched, so punctuation attached to the address --
    the full stop that ends the sentence, a comma, a closing bracket -- does
    not have to be anticipated in a pattern.
    """
    for raw in str(message or "").split():
        candidate = raw.strip(".,;:()[]<>\"'")
        if looks_like_ipv4(candidate) or looks_like_ipv6(candidate):
            return candidate
    return None


def header_value(headers, name):
    """Case-insensitive header read against a plain dict. Pure."""
    if not isinstance(headers, dict):
        return None
    wanted = str(name).lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return value
    return None


def classify_refusal(status, body_text, headers=None):
    """Sort one refusal into its cause. Pure. (state, detail).

    The decisive test for this note's cause is structural: the body contains an
    IP address. Every other 403 on this API describes a rule and names no
    address, so the classification survives GitHub rewording the sentence.
    """
    text = str(body_text or "").lower()
    if int(status or 0) not in (401, 403, 429):
        return ("not-a-refusal",
                "HTTP %s is not a refusal, so there is nothing here to sort."
                % status)
    if any(m in text for m in SECONDARY_MARKERS):
        return ("secondary-limit",
                "the body names a secondary rate limit. Wait for retry-after "
                "and slow down; no allow-list entry is involved.")
    if any(m in text for m in QUOTA_MARKERS) or header_value(
            headers, "x-ratelimit-remaining") in ("0", 0):
        return ("primary-quota-exhausted",
                "primary quota is spent. x-ratelimit-reset says when it "
                "returns, and the address is not the problem.")
    if address_in_message(body_text) is not None:
        return ("ip-allow-list",
                "the body names an IP address, which no other 403 on this API "
                "does. This is a check on where the request came from, not on "
                "what it carried.")
    if any(m in text for m in ALLOW_LIST_MARKERS):
        return ("ip-allow-list-unaddressed",
                "the body reads like an allow-list refusal but names no "
                "address. Treat the cause as the allow list and get the "
                "egress address another way.")
    if any(m in text for m in USER_AGENT_MARKERS):
        return ("user-agent-rule",
                "the body names the User-Agent header. That check runs before "
                "authentication and has its own note.")
    if int(status) == 401:
        return ("credential-rejected",
                "401 means the credential itself was not accepted. An allow "
                "list refuses with 403 and a body that names an address.")
    return ("permission-or-role",
            "no rule named itself in the body, which is what a missing "
            "permission or too low a repository role looks like.")


def ipv4_to_int(text):
    """Dotted quad to an integer, or None. Pure."""
    if not looks_like_ipv4(text):
        return None
    total = 0
    for part in str(text).split("."):
        total = (total << 8) + int(part)
    return total


def cidr_contains(cidr, address):
    """Is this address inside this CIDR. Pure. True, False or None.

    None means "not evaluated" -- an IPv6 entry, or something this script does
    not parse -- and it is deliberately not False. Reporting an unevaluated
    entry as a miss is how a script tells somebody their address is not covered
    when the entry covering it was simply one it could not read.
    """
    value = str(cidr or "").strip()
    if not value:
        return None
    if "/" in value:
        net, _, bits = value.partition("/")
        if not bits.isdigit():
            return None
        prefix = int(bits)
    else:
        net, prefix = value, 32
    if ":" in net or ":" in str(address or ""):
        return None
    left, right = ipv4_to_int(net), ipv4_to_int(address)
    if left is None or right is None or prefix > 32:
        return None
    if prefix == 0:
        return True
    mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
    return (left & mask) == (right & mask)


def covered_by(entries, address):
    """Does any active entry cover this address. Pure. (state, entry).

    Ordered so that an inactive entry that would have covered the address is
    reported as its own state. That is the most useful finding the list can
    produce: somebody already added the range and then switched it off, and a
    plain "not covered" would send you to add it a second time.
    """
    if address is None:
        return ("address-unknown", None)
    if not entries:
        return ("no-entries", None)
    inactive = None
    unevaluated = False
    for entry in entries:
        hit = cidr_contains(entry.get("value"), address)
        if hit is None:
            unevaluated = True
            continue
        if hit and entry.get("active"):
            return ("covered", entry)
        if hit:
            inactive = inactive or entry
    if inactive is not None:
        return ("covered-but-inactive", inactive)
    if unevaluated:
        return ("not-covered-some-unevaluated", None)
    return ("not-covered", None)


def egress_assumption(declared, address):
    """Compare the ranges you believe you leave from against the real one. Pure."""
    if address is None:
        return ("address-unknown",
                "no address was reported, so there is nothing to compare your "
                "declared egress against.")
    if not declared:
        return ("nothing-declared",
                "pass --egress with the ranges you believe this job leaves "
                "from and this becomes a check rather than a reading.")
    for cidr in declared:
        if cidr_contains(cidr, address) is True:
            return ("egress-as-expected",
                    "the address GitHub saw is inside %s, so your egress "
                    "assumption holds and the range simply is not allowed "
                    "yet." % cidr)
    return ("egress-assumption-wrong",
            "the address GitHub saw is outside every range you declared (%s), "
            "so adding those ranges would not have helped. Find out what this "
            "job really egresses through before asking for a change."
            % ", ".join(declared))


def paired_reading(status_here, status_elsewhere):
    """Two readings of the same call from two machines. Pure. (state, detail)."""
    here = int(status_here or 0)
    there = None if status_elsewhere is None else int(status_elsewhere)
    if there is None:
        return ("single-reading",
                "only this machine was read. Pass --seen-elsewhere with the "
                "status the same token gets from a machine that works and the "
                "network path stops being a hunch.")
    if here == 403 and there == 200:
        return ("network-path",
                "the same token is refused here and accepted there, so the "
                "difference is the source address and nothing else.")
    if here == 403 and there == 403:
        return ("refused-everywhere",
                "both addresses are refused. Either the allow list covers "
                "neither, or the cause is the credential after all.")
    if here == 200:
        return ("no-refusal",
                "this machine was not refused, so there is nothing to explain "
                "from here. Run this on the machine that fails.")
    return ("inconclusive",
            "the pair of statuses does not describe an allow list. Sort the "
            "refusal by its body first.")


def words(document):
    """Bare words in a GraphQL document. Pure. No regular expression."""
    out, current = [], ""
    for ch in str(document or ""):
        if ch.isalnum() or ch == "_":
            current += ch
        else:
            if current:
                out.append(current.lower())
            current = ""
    if current:
        out.append(current.lower())
    return out


def refuses_mutation(document):
    """Would this document change something. Pure.

    The GraphQL endpoint takes reads and writes over one verb, so the guard
    lives beside the sender rather than in a comment. This script sends one
    constant document; the guard exists so that editing the constant cannot
    quietly turn a read-only tool into a writing one.
    """
    banned = {"mutation", "subscription"}
    return bool(banned.intersection(words(document)))


def allow_list_from_graphql(body):
    """Normalise the GraphQL answer. Pure. (setting, apps_setting, entries, note)."""
    if not isinstance(body, dict):
        return (None, None, [], "no readable GraphQL body came back.")
    errors = body.get("errors") or []
    org = ((body.get("data") or {}).get("organization")) or {}
    if not org:
        detail = errors[0].get("message") if errors and isinstance(errors[0], dict) else ""
        return (None, None, [],
                "the organization block was not returned: %s. Reading an IP "
                "allow list needs admin:org-class access, so an unreadable "
                "list here means your token, not an empty list."
                % (detail or "no organization in the response"))
    nodes = ((org.get("ipAllowListEntries") or {}).get("nodes")) or []
    entries = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        entries.append({"value": node.get("allowListValue"),
                        "active": bool(node.get("isActive")),
                        "name": node.get("name") or ""})
    return (org.get("ipAllowListEnabledSetting"),
            org.get("ipAllowListForInstalledAppsEnabledSetting"),
            entries,
            "read %d entr(y/ies)." % len(entries))


def verdict(refusal_state, coverage_state, setting):
    """The finding, in one state. Pure. (state, detail)."""
    if refusal_state not in ("ip-allow-list", "ip-allow-list-unaddressed"):
        return (refusal_state,
                "this refusal is not an allow-list refusal, so the rest of "
                "this script is not about your problem.")
    if str(setting or "").upper() == "DISABLED":
        return ("allow-list-disabled",
                "the organization reports the allow list as disabled, which "
                "does not agree with the refusal. Check that you read the "
                "same organization the failing call was made against.")
    if coverage_state == "covered-but-inactive":
        return ("entry-exists-but-is-off",
                "an entry covering this address exists and is switched off. "
                "Somebody already did the work; it just is not active.")
    if coverage_state == "covered":
        return ("covered-yet-refused",
                "an active entry covers this address, so either the refusal "
                "predates the entry or the call was against a different "
                "organization. Re-run the probe before escalating.")
    if coverage_state in ("not-covered", "not-covered-some-unevaluated"):
        return ("address-not-covered",
                "no active entry covers the address GitHub saw. This is the "
                "ordinary case and the repair is one entry.")
    return ("rule-unreadable",
            "the refusal is an allow-list refusal and the list itself could "
            "not be read, which needs admin:org-class access. The cause is "
            "established; the entry that would have covered you is not.")


def repair(state, address, org):
    """The sentence a reader has to act on. Pure. Nothing here is executed."""
    if state == "entry-exists-but-is-off":
        return ("ask an owner of %s to switch the existing entry back on. "
                "Adding a second entry for the same range will not help while "
                "the first one is inactive." % org)
    if state in ("address-not-covered", "rule-unreadable"):
        return ("ask an owner of %s to add %s, or the documented egress range "
                "of this runner pool, to the organization IP allow list. For "
                "a GitHub App, enabling the App-managed allow list contributes "
                "its ranges for installation tokens. Nothing here adds "
                "anything." % (org, (address or "this job's egress range") +
                               ("/32" if address and "." in address else "")))
    if state == "covered-yet-refused":
        return ("re-run the probe from this machine. A covered address that "
                "is still refused usually means the reading and the refusal "
                "came from different places or different organizations.")
    if state == "allow-list-disabled":
        return ("confirm which organization the failing call names. A "
                "disabled list cannot produce this refusal.")
    return ("sort the refusal by its body before doing anything about "
            "addresses. This script found no allow-list refusal to repair.")


def get(session, path):
    """One GET. Returns the response object."""
    response = session.get(API + path, timeout=30)
    if response.status_code == 401:
        log.warning("401 from GitHub on %s: the credential itself was not "
                    "accepted, which is a different note", path)
    return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("org", help="the organization the calls are refused by")
    parser.add_argument("--path",
                        help="the org-scoped path to probe, default "
                             "/orgs/{org}/repos?per_page=1")
    parser.add_argument("--egress", action="append", default=[],
                        help="a CIDR you believe this job leaves from; repeatable")
    parser.add_argument("--seen-elsewhere", type=int,
                        help="the status the same token gets from a machine "
                             "that works")
    parser.add_argument("--org-allow-list", action="store_true",
                        help="read the list itself; needs admin:org-class access")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (the same read-only token the failing job holds)")
        return 2

    rest, points = read_cost(args.org_allow_list)
    log.info("read cost: %d REST request(s) against the core hourly quota, "
             "%d GraphQL point(s)", rest, points)

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub refuses requests with no User-Agent before it looks at auth.
        "User-Agent": UA,
    })

    kind = token_kind(token)
    which_list, which_detail = list_that_applies(kind)
    log.info("token: %s. %s: %s", kind, which_list, which_detail)

    path = args.path or ("/orgs/%s/repos?per_page=1" % args.org)
    probe = get(session, path)
    log.info("probe: GET %s -> HTTP %s", path, probe.status_code)
    body_text = probe.text or ""
    refusal_state, refusal_detail = classify_refusal(
        probe.status_code, body_text, dict(probe.headers))
    log.info("refusal: %s. %s", refusal_state, refusal_detail)

    address = address_in_message(body_text)
    if address:
        log.info("address GitHub saw: %s", address)

    egress_state, egress_detail = egress_assumption(args.egress, address)
    log.info("%s: %s", egress_state, egress_detail)

    setting, apps_setting, entries, note = None, None, [], "not read"
    if args.org_allow_list:
        if refuses_mutation(ALLOW_LIST_QUERY):
            log.error("the allow-list document is not a read; refusing to send it")
            return 2
        graph = session.post(API + "/graphql",
                             json={"query": ALLOW_LIST_QUERY,
                                   "variables": {"login": args.org}},
                             timeout=30)
        try:
            payload = graph.json()
        except ValueError:
            payload = None
        setting, apps_setting, entries, note = allow_list_from_graphql(payload)
        log.info("allow list: setting=%s, apps=%s, %s", setting, apps_setting, note)

    coverage_state, entry = covered_by(entries, address) if args.org_allow_list \
        else ("rule-unread", None)
    if args.org_allow_list:
        log.info("coverage: %s%s", coverage_state,
                 "" if entry is None else " by %r" % entry.get("value"))

    paired_state, paired_detail = paired_reading(probe.status_code, args.seen_elsewhere)
    log.info("paired reading: %s. %s", paired_state, paired_detail)

    state, detail = verdict(refusal_state, coverage_state, setting)
    log.info("%s: %s", state, detail)
    log.info("repair: %s", repair(state, address, args.org))

    print(json.dumps({
        "organization": args.org,
        "probe_path": path,
        "probe_status": probe.status_code,
        "token_kind": kind,
        "list_that_applies": which_list,
        "refusal_state": refusal_state,
        "address_github_saw": address,
        "declared_egress": args.egress,
        "egress_state": egress_state,
        "allow_list_setting": setting,
        "app_managed_setting": apps_setting,
        "entries_read": len(entries),
        "coverage_state": coverage_state,
        "paired_state": paired_state,
        "state": state,
        "detail": detail,
        "repair": repair(state, address, args.org),
    }, indent=2, default=str))
    return 1 if state in ("address-not-covered", "entry-exists-but-is-off",
                          "rule-unreadable") else 0


if __name__ == "__main__":
    sys.exit(main())
