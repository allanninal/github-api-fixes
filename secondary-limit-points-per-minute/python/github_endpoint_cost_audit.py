"""Compute the request rate one endpoint can sustain before it is throttled.

Read only. Every request is a GET, and the default sampled path is
/rate_limit, which does not count against the primary rate limit.

Two ceilings apply per minute to a single endpoint: 900 points, where a read
costs one point and a write costs five, and 90 seconds of CPU time per 60
seconds of real time. GitHub documents total response time as a rough estimate
of the second one, so a few timed GETs are enough to compute both and see which
binds first. That is the number a caller needs, and it is usually far lower
than 900.
"""
import argparse
import json
import logging
import os
import sys
import time

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_endpoint_cost_audit")

API = "https://api.github.com"
UA = "github-endpoint-cost-audit/1.0"

# Documented secondary limits for a single REST endpoint.
POINT_CAP = 900          # points per minute
CPU_CAP = 90.0           # seconds of CPU per 60 seconds of real time

# Reads cost one point; everything that changes state costs five.
CHEAP_METHODS = ("GET", "HEAD", "OPTIONS")


def points_for(method):
    """Documented point cost of one request. Pure.

    Anything unrecognised is charged the expensive rate. Guessing low here
    would produce a safe-looking ceiling for a request that is not safe, and
    the whole output of this script is a number people will pace against.
    """
    try:
        name = str(method).strip().upper()
    except (TypeError, ValueError):
        return 5
    return 1 if name in CHEAP_METHODS else 5


def cost_profile(samples):
    """Collapse timed samples into one entry per path. Pure.

    samples: [{"path", "method", "seconds"}, ...]

    Keeps the max as well as the mean because a path whose mean is comfortable
    and whose worst case is four times that will be throttled during the worst
    case and nowhere else, which is exactly the intermittent shape people
    struggle to reproduce.
    """
    grouped = {}
    for s in samples or []:
        try:
            path = str(s["path"])
            seconds = float(s["seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        if seconds < 0:
            continue
        entry = grouped.setdefault(path, {"path": path, "calls": 0, "total": 0.0,
                                          "max_seconds": 0.0,
                                          "points": points_for(s.get("method", "GET"))})
        entry["calls"] += 1
        entry["total"] += seconds
        entry["max_seconds"] = max(entry["max_seconds"], seconds)

    out = {}
    for path, entry in grouped.items():
        entry["mean_seconds"] = round(entry["total"] / entry["calls"], 4)
        entry["max_seconds"] = round(entry["max_seconds"], 4)
        del entry["total"]
        out[path] = entry
    return out


def safe_rate(mean_seconds, points=1, point_cap=POINT_CAP, cpu_cap=CPU_CAP):
    """Requests per minute this endpoint sustains, and which cap binds. Pure.

    The point ceiling is a constant per method. The CPU ceiling falls as the
    endpoint gets slower, and it crosses under the point ceiling at around a
    tenth of a second a call, which is why an endpoint that feels fast can
    still be throttled at a rate nowhere near 900.
    """
    try:
        seconds = float(mean_seconds)
    except (TypeError, ValueError):
        seconds = 0.0
    points = max(1, int(points))

    by_points = point_cap / points
    by_cpu = (cpu_cap / seconds) if seconds > 0 else float("inf")

    if by_cpu < by_points:
        binding, per_minute = "cpu", by_cpu
    else:
        binding, per_minute = "points", by_points

    return {"by_points": round(by_points, 1),
            "by_cpu": None if by_cpu == float("inf") else round(by_cpu, 1),
            "binding": binding, "per_minute": round(per_minute, 1),
            "mean_seconds": round(seconds, 4), "points": points}


def verdict(path, entry, safe, configured=None):
    """Compare the computed ceiling against the rate you run at. Pure."""
    mean = safe["mean_seconds"]
    ceiling = safe["per_minute"]
    cap_name = ("the 90s-of-CPU-per-60s cap" if safe["binding"] == "cpu"
                else "the 900-points-a-minute cap")

    if configured is None:
        return ("ceiling",
                "%s costs %.3f s a call, so %s allows about %d request(s) a "
                "minute on this path." % (path, mean, cap_name, ceiling))

    try:
        configured = float(configured)
    except (TypeError, ValueError):
        return ("ceiling", "%s allows about %d a minute; no configured rate "
                           "was given to compare it against." % (path, ceiling))

    if configured > ceiling:
        return ("over-budget",
                "%s is configured for %d a minute against a ceiling of %d. %s "
                "binds first at %.3f s a call, so the surplus is refused, "
                "retried, and refused again."
                % (path, configured, ceiling, cap_name, mean))

    if configured >= ceiling * 0.8:
        return ("near-budget",
                "%s runs at %d a minute against a ceiling of %d. One slower "
                "response, or one worst case of %.3f s, closes that gap."
                % (path, configured, ceiling, entry.get("max_seconds", mean)))

    if mean >= 1.0:
        return ("expensive",
                "%s costs %.3f s a call, which caps it at %d a minute however "
                "little you are asking for today. Treat it as a path to move "
                "work off rather than a path to pace." % (path, mean, ceiling))

    return ("clear",
            "%s runs at %d a minute against a ceiling of %d, %s binding."
            % (path, configured, ceiling, cap_name))


def sample_path(session, path, count, pause):
    """Time a few sequential GETs. Sequential and paced on purpose: a sampler
    that fans out would measure the limit it is trying to describe."""
    url = API + path if path.startswith("/") else path
    samples, resource, throttled = [], None, False
    for i in range(count):
        if i:
            time.sleep(pause)
        start = time.monotonic()
        try:
            r = session.get(url, timeout=60)
        except requests.RequestException as exc:
            log.warning("%s sample %d failed: %s", path, i, exc)
            continue
        elapsed = time.monotonic() - start
        headers = {k.lower(): v for k, v in r.headers.items()}
        resource = resource or headers.get("x-ratelimit-resource")
        if r.status_code in (403, 429) and "secondary rate limit" in r.text.lower():
            throttled = True
            log.warning("%s was throttled while being measured; retry-after %s",
                        path, headers.get("retry-after", "absent"))
            continue
        if r.status_code >= 400:
            log.warning("%s sample %d returned %d", path, i, r.status_code)
            continue
        samples.append({"path": path, "method": "GET", "seconds": elapsed})
    return samples, resource, throttled


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", action="append", default=None,
                    help="path to measure; repeatable (default /rate_limit)")
    ap.add_argument("--samples", type=int, default=4,
                    help="timed requests per path")
    ap.add_argument("--pause", type=float, default=1.0,
                    help="seconds between samples")
    ap.add_argument("--rate", type=float, default=None,
                    help="requests per minute your job runs at on these paths")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    paths = args.path or ["/rate_limit"]
    billed = [p for p in paths if p.rstrip("/") != "/rate_limit"]
    if billed:
        log.warning("measuring %d path(s) that do cost quota: %d sample(s) "
                    "each, one point per sample", len(billed),
                    args.samples)

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    all_samples, resources, worst = [], {}, "clear"
    findings = []
    for path in paths:
        samples, resource, throttled = sample_path(session, path, max(1, args.samples),
                                                   max(0.0, args.pause))
        all_samples.extend(samples)
        if resource:
            resources[path] = resource
        if throttled:
            log.warning("%s tripped a secondary limit during measurement, which "
                        "is itself the finding: the endpoint is already over "
                        "budget at the rate this sampler used", path)

    profile = cost_profile(all_samples)
    if not profile:
        log.error("no successful samples, so there is nothing to cost")
        return 2

    ranked = sorted(profile.values(), key=lambda e: e["mean_seconds"], reverse=True)
    for entry in ranked:
        safe = safe_rate(entry["mean_seconds"], entry["points"])
        state, detail = verdict(entry["path"], entry, safe, args.rate)
        findings.append({"path": entry["path"], "state": state,
                         "mean_seconds": entry["mean_seconds"],
                         "max_seconds": entry["max_seconds"],
                         "billed_to": resources.get(entry["path"]), **safe})
        log.info("%-14s %s", state, detail)
        if resources.get(entry["path"]):
            log.info("               billed to the %s bucket",
                     resources[entry["path"]])
        if state in ("over-budget", "near-budget", "expensive"):
            worst = state if worst == "clear" else worst

    if worst != "clear":
        log.info("repair: replace per-item calls on the expensive path with one "
                 "GraphQL query returning the same fields, which is billed to a "
                 "different allowance entirely.")
        log.info("repair: raise per_page to 100 on list endpoints so the same "
                 "data arrives in a third of the calls.")
        log.info("repair: send If-None-Match with the stored etag. A 304 costs "
                 "the server almost nothing and costs you nothing at all.")
        log.info("repair: where the calls are unavoidable, spread them across "
                 "the minute rather than bursting, and on a throttled response "
                 "sleep the whole retry-after before resuming that path.")

    print(json.dumps({"findings": findings, "configured_per_minute": args.rate},
                     indent=2))
    return 1 if worst == "over-budget" else 0


if __name__ == "__main__":
    sys.exit(main())
