"""Find one webhook URL registered by more than one GitHub hook.

Read only. Org hooks and repo hooks are independent objects, so the same URL can
be registered in both scopes and every overlapping event is then delivered twice.
The script prints which hook to remove; it never removes one.
"""
import argparse
import logging
import os
import sys
from urllib.parse import urlsplit

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_duplicate_hooks")

API = "https://api.github.com"
UA = "github-duplicate-hooks/1.0"

# A hook subscribed to "*" receives every event type, current and future, so it
# intersects with anything the other hook on the same URL carries.
WILDCARD = "*"


def endpoint(url):
    """Reduce a webhook URL to lowercase host plus path. Pure.

    Two hooks created years apart by different people differ cosmetically far
    more often than they differ meaningfully: a trailing slash, a capitalised
    host, http where the other is https. All of those deliver to the same server,
    and a raw string comparison across them finds nothing and reports a clean
    account.
    """
    if not url:
        return ""
    parts = urlsplit(str(url).strip())
    host = (parts.hostname or "").lower()
    if not host:
        return str(url).strip().lower().rstrip("/")
    port = ":%d" % parts.port if parts.port not in (None, 80, 443) else ""
    return host + port + (parts.path or "").rstrip("/")


def overlap(a, b):
    """Events both hooks carry, as a sorted list. Pure.

    A wildcard subscribes to everything, so it overlaps whatever the other hook
    lists; two wildcards overlap on everything and are reported as such.
    """
    sa, sb = set(a or []), set(b or [])
    if WILDCARD in sa and WILDCARD in sb:
        return [WILDCARD]
    if WILDCARD in sa:
        return sorted(sb)
    if WILDCARD in sb:
        return sorted(sa)
    return sorted(sa & sb)


def group(hooks):
    """Group hooks by endpoint and classify each group. Pure.

    hooks: dicts with source, id, url, events and active.
    Returns rows sorted by endpoint, each with a state:

      unique    one hook, nothing to do
      duplicate two or more active hooks with events in common
      latent    a second hook exists but is inactive; re-enabling doubles delivery
      disjoint  several hooks on one URL that deliberately split the events
    """
    by_endpoint = {}
    for h in hooks or []:
        by_endpoint.setdefault(endpoint(h.get("url")), []).append(h)

    rows = []
    for target, members in sorted(by_endpoint.items()):
        active = [m for m in members if m.get("active", True)]
        shared = []
        for i, first in enumerate(active):
            for second in active[i + 1:]:
                shared.extend(e for e in overlap(first.get("events"),
                                                 second.get("events"))
                              if e not in shared)
        if len(members) == 1:
            state = "unique"
        elif len(active) < 2:
            state = "latent"
        elif shared:
            state = "duplicate"
        else:
            state = "disjoint"
        rows.append({"endpoint": target, "state": state, "hooks": members,
                     "shared": sorted(shared)})
    return rows


def guid_pairs(logs):
    """Do the copies share a delivery guid? Pure.

    logs: {source: [delivery, ...]} for one endpoint. Returns counts of guids
    seen under more than one source, and of (event, minute) slots covered by two
    sources under different guids. The first says guid-based idempotency already
    handles this; the second says it does not and the key has to come from the
    payload.
    """
    sources_by_guid = {}
    slots = {}
    for source, deliveries in (logs or {}).items():
        for d in deliveries or []:
            guid = d.get("guid")
            if guid:
                sources_by_guid.setdefault(guid, set()).add(source)
            when = str(d.get("delivered_at") or "")[:16]
            if when:
                slots.setdefault((str(d.get("event") or ""), when), {})[source] = guid
    shared = sum(1 for s in sources_by_guid.values() if len(s) > 1)
    twinned = sum(1 for seen in slots.values()
                  if len(seen) > 1 and len(set(seen.values())) > 1)
    return {"shared_guids": shared, "same_event_different_guid": twinned}


def next_link(response):
    """The rel=next URL from the Link header, or None."""
    for part in (response.headers.get("Link") or "").split(","):
        chunk = part.strip()
        if chunk.startswith("<") and chunk.endswith('rel="next"'):
            return chunk[1:chunk.index(">")]
    return None


def get(session, url, **params):
    r = session.get(url, params=params, timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, expired or "
                         "malformed")
    if r.status_code in (403, 404):
        raise SystemExit("%d from %s: repository hooks need admin:repo_hook and "
                         "organization hooks need admin:org_hook; GitHub answers "
                         "404 rather than 403 when the token cannot see the "
                         "resource" % (r.status_code, url))
    r.raise_for_status()
    return r


def page(session, url, limit=500, **params):
    out = []
    while url and len(out) < limit:
        r = get(session, url, **params)
        out.extend(r.json())
        url, params = next_link(r), {}
    return out[:limit]


def collect(session, scopes):
    """Flatten every hook from every scope into the shape group() expects."""
    hooks = []
    for label, base in scopes:
        for h in page(session, base, per_page=100):
            hooks.append({
                "source": label,
                "base": base,
                "id": h.get("id"),
                "url": (h.get("config") or {}).get("url"),
                "events": h.get("events") or [],
                "active": bool(h.get("active", True)),
            })
    return hooks


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--org", action="append", default=[],
                    help="organization login; repeat for several orgs")
    ap.add_argument("--repo", action="append", default=[],
                    help="owner/name; repeat for several repositories")
    ap.add_argument("--max-deliveries", type=int, default=100,
                    help="deliveries to read per hook when checking whether the "
                         "copies share a guid (0 to skip)")
    args = ap.parse_args()

    if not (args.org or args.repo):
        log.error("pass at least one --org login or --repo owner/name")
        return 2

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    scopes = []
    for org in args.org:
        scopes.append(("org " + org, "%s/orgs/%s/hooks" % (API, org)))
    for repo in args.repo:
        owner, _, name = repo.partition("/")
        if not (owner and name):
            log.error("--repo takes owner/name, for example acme/api")
            return 2
        scopes.append(("repo " + repo, "%s/repos/%s/%s/hooks" % (API, owner, name)))

    hooks = collect(session, scopes)
    rows = group(hooks)

    duplicated = latent = 0
    for row in rows:
        members = ", ".join("%s#%s%s" % (m["source"], m["id"],
                                         "" if m["active"] else " (inactive)")
                            for m in row["hooks"])
        line = "%-10s %s  %s" % (row["state"], row["endpoint"] or "?", members)
        if row["state"] in ("unique", "disjoint"):
            log.info(line)
            if row["state"] == "disjoint":
                log.info("  no shared events: a deliberate split, not a duplicate")
            continue

        log.warning(line)
        if row["state"] == "latent":
            latent += 1
            log.warning("  only one hook is active. Re-enabling the other "
                        "doubles delivery of: %s",
                        ", ".join(overlap(row["hooks"][0]["events"],
                                          row["hooks"][-1]["events"])) or "nothing")
            continue

        duplicated += 1
        log.warning("  delivered twice: %s", ", ".join(row["shared"]))
        if args.max_deliveries:
            logs = {}
            for m in row["hooks"]:
                logs[m["source"]] = page(
                    session, "%s/%s/deliveries" % (m["base"], m["id"]),
                    limit=args.max_deliveries, per_page=100)
            pairs = guid_pairs(logs)
            log.warning("  %d guid(s) seen under more than one hook, %d event(s) "
                        "arriving twice under different guids",
                        pairs["shared_guids"], pairs["same_event_different_guid"])
            if pairs["same_event_different_guid"]:
                log.warning("  deduplicating on X-GitHub-Delivery will not catch "
                            "these; key the side effect on something in the "
                            "payload instead")
        log.warning("  repair: keep one source of truth and delete the other "
                    "hook by hand (DELETE is not something this script will do)")

    log.info("%d hook(s) across %d endpoint(s), %d duplicated, %d latent",
             len(hooks), len(rows), duplicated, latent)
    return 1 if duplicated else 0


if __name__ == "__main__":
    sys.exit(main())
