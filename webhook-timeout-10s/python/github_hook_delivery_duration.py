"""Report how close a webhook receiver is to the 10 second delivery cutoff.

Read only. GETs the repository's hooks and their delivery records. Nothing is
sent to the receiver: timing it from here would be a write, and it would be a
worse measurement than the one GitHub already recorded from its own side of the
connection.

GitHub allows a receiver ten seconds to respond and files anything slower as a
failed delivery, whatever the handler eventually does. Every delivery record
carries a duration, on the successful attempts as much as the failed ones, so
the slide toward the cutoff is readable before the first failure.

This is a different read from a delivery failure audit. That one buckets by
status code. This one ignores status codes except the abandonment marker and
reads the duration column on everything.

Environment:

    GITHUB_TOKEN   a read-only token with access to the repository
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_hook_delivery_duration")

API = "https://api.github.com"
UA = "github-hook-delivery-duration/1.0"

CUTOFF_MS = 10000
WARN_MS = 8000
SLOW_MS = 5000
# The duration field carries no unit. Nothing survives past ten seconds and
# nothing real takes sixty thousand of them, so a value at or under sixty is
# seconds and anything above it is already milliseconds.
SECONDS_CEILING = 60


def duration_ms(row):
    """A delivery's duration in milliseconds, or None. Pure."""
    if not isinstance(row, dict):
        return None
    raw = row.get("duration")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return value * 1000.0 if value <= SECONDS_CEILING else value


def timed_out(row):
    """Whether GitHub abandoned this delivery. Pure.

    The status text is the reliable marker. A record can also carry a duration
    at the cutoff with no status text at all, which counts too, because that is
    the same event described by the other column.
    """
    if not isinstance(row, dict):
        return False
    status = " ".join(str(row.get("status") or "").lower().split())
    if "timed out" in status or "timeout" in status:
        return True
    ms = duration_ms(row)
    return ms is not None and ms >= CUTOFF_MS


def classify(row):
    """Sort one delivery by how much room it had left. Pure."""
    if timed_out(row):
        return "timed-out"
    ms = duration_ms(row)
    if ms is None:
        return "unknown"
    if ms >= WARN_MS:
        return "at-risk"
    if ms >= SLOW_MS:
        return "slow"
    return "fine"


def percentile(values, p):
    """Nearest-rank percentile over a list of numbers, or None. Pure.

    Nearest rank rather than interpolation on purpose: a delivery window is
    small and every value in it is a real measurement, so reporting a number
    that no delivery actually took would be a worse answer than reporting one
    that a delivery did.
    """
    numbers = sorted(v for v in (values or []) if isinstance(v, (int, float)))
    if not numbers:
        return None
    if p <= 0:
        return numbers[0]
    if p >= 100:
        return numbers[-1]
    import math
    rank = max(1, math.ceil(p / 100.0 * len(numbers)))
    return numbers[min(rank, len(numbers)) - 1]


def stats(rows):
    """The distribution that decides the verdict. Pure."""
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    measured = [duration_ms(r) for r in rows]
    measured = [m for m in measured if m is not None]
    p95 = percentile(measured, 95)
    return {
        "count": len(rows),
        "measured": len(measured),
        "timed_out": sum(1 for r in rows if timed_out(r)),
        "p50": percentile(measured, 50),
        "p95": p95,
        "max": max(measured) if measured else None,
        "headroom_ms": None if p95 is None else CUTOFF_MS - p95,
    }


def by_event(rows, min_count=3):
    """The same distribution per event type. Pure."""
    groups = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        event = str(row.get("event") or "unknown").strip().lower() or "unknown"
        groups.setdefault(event, []).append(row)
    out = {}
    for event, group in groups.items():
        measured = [m for m in (duration_ms(r) for r in group) if m is not None]
        out[event] = {"count": len(group),
                      "timed_out": sum(1 for r in group if timed_out(r)),
                      "p95": percentile(measured, 95)}
    return {k: v for k, v in out.items() if v["count"] >= min_count or v["timed_out"]}


def slowest_event(rows, min_count=3):
    """The event type with the worst tail, or None. Pure."""
    grouped = by_event(rows, min_count)
    ranked = [(v["p95"], k, v) for k, v in grouped.items() if v["p95"] is not None]
    if not ranked:
        return None
    ranked.sort(key=lambda t: (-t[0], t[1]))
    p95, event, row = ranked[0]
    return {"event": event, "p95": p95, "count": row["count"],
            "timed_out": row["timed_out"]}


def verdict(st):
    """Turn the distribution into a finding. Pure."""
    if not st or not st.get("count"):
        return ("no-data",
                "no deliveries in the retained window, so there is nothing to "
                "measure. That is not the same as a receiver that is fast.")
    if not st.get("measured"):
        return ("no-durations",
                "%d delivery/deliveries carry no duration, so the tail cannot "
                "be measured from this feed." % st["count"])
    p95 = st["p95"]
    if st["timed_out"]:
        return ("timing-out",
                "%d deliveries were abandoned at the 10 second cutoff, and the "
                "95th percentile is %dms, which leaves %dms of headroom on "
                "everything else." % (st["timed_out"], p95, st["headroom_ms"]))
    if p95 >= WARN_MS:
        return ("at-the-edge",
                "nothing has timed out yet and the 95th percentile is %dms, "
                "leaving %dms before the cutoff. This fails on the next slow "
                "week." % (p95, st["headroom_ms"]))
    if p95 >= SLOW_MS:
        return ("slow",
                "the 95th percentile is %dms against a 10 second cutoff. The "
                "handler is doing real work inline and has %dms of room."
                % (p95, st["headroom_ms"]))
    return ("healthy",
            "the 95th percentile is %dms, %dms inside the cutoff."
            % (p95, st["headroom_ms"]))


def repair(state, worst=None):
    """The sentence a reader has to act on. Pure."""
    if state in ("timing-out", "at-the-edge", "slow"):
        target = (" Start with %s, whose 95th percentile is %dms."
                  % (worst["event"], worst["p95"])) if worst else ""
        return ("verify the signature, put the raw payload on a queue, return "
                "202, and do the work in a worker keyed on the delivery guid so "
                "a redelivery cannot run it twice.%s" % target)
    if state == "no-data":
        return ("nothing to repair, and nothing proved either. Check the hook "
                "is active and that the retention window covers a period when "
                "events actually happened.")
    if state == "no-durations":
        return ("read the durations from a wider page of deliveries; this "
                "window has statuses but no timings to work from.")
    return "nothing. The receiver answers well inside the cutoff."


def next_link(headers):
    """The rel=next URL from a Link header, or None. Pure."""
    link = (headers or {}).get("Link") or (headers or {}).get("link") or ""
    for part in str(link).split(","):
        section = part.split(";")
        if len(section) < 2:
            continue
        url = section[0].strip()
        if 'rel="next"' in part and url.startswith("<") and url.endswith(">"):
            return url[1:-1]
    return None


def get(session, url):
    """One GET. Returns (status, json-or-None, headers)."""
    full = API + url if url.startswith("/") else url
    r = session.get(full, timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body, r.headers


def deliveries(session, owner, repo, hook_id, pages=8):
    """Delivery records for one hook, following the cursor. Read only."""
    url = "/repos/%s/%s/hooks/%s/deliveries?per_page=100" % (owner, repo, hook_id)
    out = []
    for _ in range(pages):
        status, body, headers = get(session, url)
        if status != 200 or not isinstance(body, list):
            log.error("deliveries for hook %s returned %d", hook_id, status)
            break
        out.extend(body)
        url = next_link(headers)
        if not url:
            break
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="owner/repo")
    ap.add_argument("--hook-id", default=None, help="one hook; omit for all")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN to a read-only token that can read the "
                  "repository's hooks")
        return 2
    if "/" not in args.repo:
        log.error("--repo takes owner/repo")
        return 2
    owner, repo = args.repo.split("/", 1)

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    status, hooks, _ = get(session, "/repos/%s/%s/hooks?per_page=100" % (owner, repo))
    if status != 200 or not isinstance(hooks, list):
        log.error("GET hooks returned %d", status)
        return 2
    if args.hook_id:
        hooks = [h for h in hooks if str(h.get("id")) == str(args.hook_id)]

    report = []
    worst_state = "healthy"
    for hook in hooks:
        hook_id = hook.get("id")
        url = (hook.get("config") or {}).get("url", "?")
        log.info("hook %s -> %s", hook_id, url)
        rows = deliveries(session, owner, repo, hook_id)
        st = stats(rows)
        state, detail = verdict(st)
        worst = slowest_event(rows)
        log.info("%d delivery/deliveries, %d timed out, p50 %sms, p95 %sms, "
                 "max %sms", st["count"], st["timed_out"],
                 int(st["p50"]) if st["p50"] is not None else "?",
                 int(st["p95"]) if st["p95"] is not None else "?",
                 int(st["max"]) if st["max"] is not None else "?")
        log.info("%s: %s", state, detail)
        if worst:
            log.info("slowest event: %s, p95 %dms across %d deliveries",
                     worst["event"], worst["p95"], worst["count"])
        log.info("repair: %s", repair(state, worst))
        if state in ("timing-out", "at-the-edge"):
            worst_state = state
        report.append({"hook_id": hook_id, "url": url, "stats": st,
                       "state": state, "detail": detail,
                       "slowest_event": worst,
                       "by_event": by_event(rows)})

    print(json.dumps({"repo": args.repo, "hooks": report}, indent=2, default=str))
    return 1 if worst_state != "healthy" else 0


if __name__ == "__main__":
    sys.exit(main())
