"""Report GitHub webhooks whose deliveries are failing, and say how they fail.

Read only. Every request is a GET, so a token with read access to the repository
and its hooks is enough. The redelivery call is printed for a human to run, never
made here: this script holds a credential that can reach your repositories.
"""
import argparse
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_hook_delivery_audit")

API = "https://api.github.com"
UA = "github-hook-delivery-audit/1.0"

# Failure buckets, most diagnostic first. Ties in the dominant-bucket scan are
# broken by this order, so a hook failing equally often on two causes reports the
# one that names a specific repair rather than the one that says "other".
FAILURE_ORDER = ("rejected", "server-error", "timeout", "unreachable",
                 "client-error", "unknown")


def bucket(delivery):
    """Sort one delivery record into a bucket. Pure.

    status_code is the diagnosis. A record with no code at all never reached a
    server, which is a network problem; a record with 401 or 403 reached one that
    refused it, which is usually a signature the receiver would not accept. These
    want opposite repairs and are routinely reported as one number.
    """
    status = str(delivery.get("status") or "").strip().lower()
    raw = delivery.get("status_code")
    try:
        code = int(raw)
    except (TypeError, ValueError):
        code = 0

    if 200 <= code < 300:
        return "ok"
    if "tim" in status:
        return "timeout"
    if not code:
        return "unreachable"
    if code in (401, 403):
        return "rejected"
    if 400 <= code < 500:
        return "client-error"
    if 500 <= code < 600:
        return "server-error"
    return "unknown"


def triage(hook):
    """Read the hook's last_response, which is the one-request version of this.

    Pure. code is null on a hook that has never delivered anything, which is not
    a failure and must not be reported as one: it means the hook is new, is
    inactive, or is subscribed to events that have not happened.
    """
    last = hook.get("last_response") or {}
    code = last.get("code")
    if code is None:
        return ("never", "no delivery attempt recorded yet")
    try:
        code = int(code)
    except (TypeError, ValueError):
        return ("unknown", "unreadable last_response code %r" % (last.get("code"),))
    if 200 <= code < 300:
        return ("ok", "last attempt returned %d" % code)
    message = str(last.get("message") or "").strip()
    return ("failing", "last attempt returned %d%s"
            % (code, ": " + message if message else ""))


def summarize(deliveries):
    """Count deliveries by bucket and keep the ends of the window. Pure.

    delivered_at is ISO 8601 in UTC on every record, so string comparison orders
    them correctly and nothing needs parsing to find the first and last of each.
    """
    out = {"total": 0, "ok": 0, "failed": 0, "redeliveries": 0, "counts": {},
           "guids": {}, "last_ok": None, "first_failed": None, "last_failed": None}
    for d in deliveries or []:
        kind = bucket(d)
        when = str(d.get("delivered_at") or "")
        out["total"] += 1
        if d.get("redelivery"):
            out["redeliveries"] += 1
        if kind == "ok":
            out["ok"] += 1
            if when and (out["last_ok"] is None or when > out["last_ok"]):
                out["last_ok"] = when
            continue
        out["failed"] += 1
        out["counts"][kind] = out["counts"].get(kind, 0) + 1
        ids = out["guids"].setdefault(kind, [])
        if len(ids) < 5 and d.get("id") is not None:
            ids.append(d.get("id"))
        if when:
            if out["first_failed"] is None or when < out["first_failed"]:
                out["first_failed"] = when
            if out["last_failed"] is None or when > out["last_failed"]:
                out["last_failed"] = when
    return out


def verdict(summary):
    """Classify one hook from its delivery summary. Pure.

    Returns (state, detail). "recovered" exists because a fixed hook and a
    broken one produce the same failure count, and the difference between them
    is whether anything has succeeded since.
    """
    total = int(summary.get("total") or 0)
    if not total:
        return ("empty",
                "no deliveries in the retained window. Either nothing this hook "
                "subscribes to has happened, or the hook is not active.")

    failed = int(summary.get("failed") or 0)
    if not failed:
        return ("clean", "%d delivery(ies), all accepted" % total)

    last_ok = summary.get("last_ok")
    last_failed = summary.get("last_failed")
    if last_ok and last_failed and last_ok > last_failed:
        return ("recovered",
                "%d of %d failed, but the most recent delivery succeeded. The "
                "receiver is working; %d event(s) are still waiting on a replay."
                % (failed, total, failed))

    counts = summary.get("counts") or {}
    worst = None
    for kind in FAILURE_ORDER:
        n = counts.get(kind, 0)
        if n and (worst is None or n > counts[worst]):
            worst = kind
    n = counts.get(worst, 0)

    if worst == "rejected":
        return (worst,
                "%d of %d came back 401 or 403. Your own server refused GitHub. "
                "This is the only shape a mismatched webhook secret takes from "
                "outside: the API will not compare secrets for you." % (n, total))
    if worst == "server-error":
        return (worst,
                "%d of %d returned 5xx. The payload arrived and the handler "
                "raised, so the trace is in your application, not in the "
                "network." % (n, total))
    if worst == "timeout":
        return (worst,
                "%d of %d timed out. GitHub allows a receiver 10 seconds; a "
                "handler doing its real work synchronously runs past that as "
                "soon as the payload grows." % (n, total))
    if worst == "unreachable":
        return (worst,
                "%d of %d recorded no status code at all, so nothing answered: "
                "DNS, TLS, a closed port, or an allow-list that no longer "
                "matches GitHub's hook ranges." % (n, total))
    return (worst or "unknown",
            "%d of %d failed with a 4xx that is not an auth error, which is "
            "usually a route that moved (404) or a body the handler would not "
            "parse (400)." % (n, total))


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
        raise SystemExit("%d from %s: reading hooks needs admin:repo_hook (or "
                         "the fine-grained Webhooks: Read permission). GitHub "
                         "returns 404 rather than 403 when a token cannot see a "
                         "resource at all." % (r.status_code, url))
    r.raise_for_status()
    return r


def page(session, url, key=None, limit=1000, **params):
    """Follow Link rel=next until the limit. Returns a flat list."""
    out = []
    while url and len(out) < limit:
        r = get(session, url, **params)
        body = r.json()
        out.extend(body if isinstance(body, list) else body.get(key) or [])
        url, params = next_link(r), {}
    return out[:limit]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--hook", type=int, default=None,
                    help="only this hook id (default: every hook on the repo)")
    ap.add_argument("--max-deliveries", type=int, default=300,
                    help="stop paging each hook's delivery log after this many")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    owner, _, name = args.repo.partition("/")
    if not (owner and name):
        log.error("--repo takes owner/name, for example acme/api")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    base = "%s/repos/%s/%s/hooks" % (API, owner, name)
    hooks = page(session, base, per_page=100)
    if args.hook:
        hooks = [h for h in hooks if h.get("id") == args.hook]
    if not hooks:
        log.info("no webhooks on %s that this token can see", args.repo)
        return 0

    failing = replayable = 0
    for hook in hooks:
        hid = hook.get("id")
        url = (hook.get("config") or {}).get("url", "?")
        state, detail = triage(hook)
        log.info("hook %s %s  last_response: %s (%s)", hid, url, state, detail)

        deliveries = page(session, "%s/%s/deliveries" % (base, hid),
                          limit=args.max_deliveries, per_page=100)
        summary = summarize(deliveries)
        state, detail = verdict(summary)
        line = "  %-12s %s" % (state, detail)
        if state in ("clean", "empty"):
            log.info(line)
            continue

        log.warning(line)
        log.warning("  failures from %s to %s, %d redelivery(ies) already in "
                    "the log", summary["first_failed"], summary["last_failed"],
                    summary["redeliveries"])
        if state != "recovered":
            failing += 1
        replayable += summary["failed"]
        for kind, ids in sorted(summary["guids"].items()):
            for did in ids:
                log.warning("  repair: POST %s/%s/deliveries/%s/attempts  "
                            "(%s)", base, hid, did, kind)

    log.info("%d hook(s), %d failing, %d delivery(ies) needing a replay",
             len(hooks), failing, replayable)
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
