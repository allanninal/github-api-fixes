"""Tell an expensive request that GitHub gave up on from an incident.

Read only. Two timed GETs against the path under test, plus one free baseline
against GET /rate_limit. Nothing is written and the repair is printed rather
than performed.

GitHub terminates a request it cannot serve in about ten seconds and answers
with a gateway error rather than a 4xx. Retry logic reads 5xx as transient and
sends the identical expensive request again, which costs the same ten seconds
and fails the same way. The repair is to make the request smaller, so this
script prints a narrowed version of your parameters rather than a backoff.

What this can and cannot see: the API does not report a per-request time budget
anywhere, so the cutoff is inferred from elapsed time. A gateway error that
arrives quickly is an incident rather than a cost problem, and the script says
so rather than sending you to rewrite a query that was fine.

Environment:

    GITHUB_TOKEN    a token with read access to the repository
"""
import argparse
import json
import logging
import os
import sys
import time

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_timeout_502")

API = "https://api.github.com"
UA = "github-timeout-502/1.0"

# The server-side budget for a single request, in seconds. Approximate by
# nature: it is not published as a header and cannot be read from a response.
CUTOFF_SECONDS = 10.0
# How close to the cutoff still counts as "this ran out of time". A success at
# nine seconds is not healthy, it is next week's failure.
TOLERANCE = 2.0

# The statuses a killed request comes back as. 500 is deliberately not here: it
# is a different failure and calling it a timeout would be a guess.
GATEWAY = (502, 503, 504)

# The largest page size worth suggesting when nobody set one.
MAX_PER_PAGE = 100


def lower_headers(headers):
    """Headers keyed by lowercase name. Pure.

    HTTP header names are case-insensitive and every client returns them in a
    different case, so a check that reads Retry-After from a dict typed by hand
    finds nothing and reports a timeout that was really a throttle.
    """
    return {str(k).lower(): v for k, v in (headers or {}).items()}


def request_id(headers):
    """The value support will ask for, or None. Pure."""
    return lower_headers(headers).get("x-github-request-id")


def is_gateway(status):
    """Whether this status is the shape a killed request comes back as. Pure."""
    try:
        return int(status) in GATEWAY
    except (TypeError, ValueError):
        return False


def is_throttled(status, headers):
    """Whether the response is a rate limit rather than a timeout. Pure.

    Checked before anything else, because a throttle misfiled as a timeout
    sends you off rewriting a query that was never the problem. The two are
    separated by headers, not by status: a secondary limit answers 403 or 429
    with retry-after, and an exhausted primary quota answers with a remaining
    count of zero.
    """
    h = lower_headers(headers)
    try:
        code = int(status)
    except (TypeError, ValueError):
        return False
    if code not in (403, 429):
        return False
    return "retry-after" in h or str(h.get("x-ratelimit-remaining", "")).strip() == "0"


def near_cutoff(elapsed, cutoff=CUTOFF_SECONDS, tolerance=TOLERANCE):
    """Whether this call ran long enough to have been killed for it. Pure."""
    try:
        return float(elapsed) >= float(cutoff) - float(tolerance)
    except (TypeError, ValueError):
        return False


def classify(status, elapsed, headers=None):
    """Classify one timed attempt. Pure. Returns (state, detail)."""
    try:
        secs = float(elapsed)
    except (TypeError, ValueError):
        secs = None

    if status is None:
        if secs is not None and secs >= CUTOFF_SECONDS:
            return ("client-timeout",
                    "your own client gave up after %.1fs, which is at or past "
                    "the server's own budget, so there is no response to read."
                    % secs)
        return ("unknown", "the attempt produced neither a status nor a usable "
                           "elapsed time.")

    try:
        code = int(status)
    except (TypeError, ValueError):
        return ("unknown", "the attempt produced no readable status.")

    if is_throttled(code, headers):
        return ("throttled",
                "%d carries rate-limit headers, so this is a throttle and not a "
                "timeout. The response says how long to wait and waiting is the "
                "repair." % code)

    if is_gateway(code):
        if secs is not None and near_cutoff(secs):
            return ("timeout",
                    "%d came back after %.1fs, at the cutoff GitHub applies to "
                    "a single request. The query is too expensive to serve, not "
                    "unlucky." % (code, secs))
        return ("gateway-early",
                "%d came back after %.1fs, far short of the cutoff, so this is "
                "not your query running out of time. Check the status page "
                "before rewriting anything." % (code, secs if secs is not None else -1.0))

    if 500 <= code < 600:
        return ("server-other",
                "%d is a server error of a different shape. It is not the "
                "per-request cutoff and it is not a throttle." % code)

    if 400 <= code < 500:
        return ("client-error",
                "%d is a client error, so the request was understood and "
                "refused rather than abandoned partway through." % code)

    if secs is not None and near_cutoff(secs):
        return ("slow-success",
                "the call answered %d in %.1fs, inside the tolerance of the "
                "%.0fs cutoff. It works today and fails on the week the "
                "repository grows." % (code, secs, CUTOFF_SECONDS))

    return ("ok",
            "the call answered %d in %.1fs, comfortably inside the cutoff."
            % (code, secs if secs is not None else -1.0))


def retry_repeats_it(state):
    """Whether sending the identical request again reproduces this. Pure."""
    return state in ("timeout", "client-timeout")


def wasted_retries(state, retries):
    """Attempts a retry wrapper would spend to no purpose at all. Pure."""
    try:
        n = int(retries)
    except (TypeError, ValueError):
        return 0
    return max(0, n) if retry_repeats_it(state) else 0


def narrow(params):
    """A cheaper version of the same request. Pure.

    Halving the page size is the one narrowing that applies to every list
    endpoint without knowing anything about the query. Everything else worth
    trying is specific to the call and is printed as prose instead.
    """
    out = dict(params or {})
    try:
        size = int(out.get("per_page", MAX_PER_PAGE))
    except (TypeError, ValueError):
        size = MAX_PER_PAGE
    out["per_page"] = max(1, size // 2)
    return out


def narrowing_exhausted(params):
    """Whether the page size can no longer be halved. Pure."""
    try:
        return int((params or {}).get("per_page", MAX_PER_PAGE)) <= 1
    except (TypeError, ValueError):
        return False


def repair(state, params=None):
    """The sentence a reader has to act on. Pure."""
    if state == "timeout":
        base = ("make the request cheaper rather than sending it again: halve "
                "per_page, add a date or path filter, split a comparison into "
                "ranges, or ask GraphQL for only the fields you need. Record "
                "x-github-request-id from the failing response first, because "
                "the retry destroys it.")
        if narrowing_exhausted(params):
            return base + " The page size is already at 1, so the request has "\
                          "to be split by range or path instead."
        return base
    if state == "client-timeout":
        return ("raise your own client timeout above the server's budget and "
                "run this again. Until you wait longer than GitHub does you are "
                "diagnosing your own deadline, not GitHub's.")
    if state == "gateway-early":
        return ("retry this one and check the status page. A gateway error that "
                "arrives in a fraction of a second is not your query running "
                "out of time.")
    if state == "throttled":
        return ("wait exactly as long as the response tells you to. This is the "
                "rate-limit path, it has its own repair, and rewriting the "
                "query will not change it.")
    if state == "slow-success":
        return ("narrow it now, while it still works. A call this close to the "
                "cutoff crosses it on the busiest day of the quarter.")
    if state == "server-other":
        return ("retry once, then take x-github-request-id to support. This is "
                "neither the per-request cutoff nor a throttle.")
    if state == "client-error":
        return "read the status: the request was refused, not abandoned."
    if state == "ok":
        return "nothing."
    return "give the probe a path it can reach and a timeout longer than 10s."


def read_cost(paths, attempts=2):
    """Requests this run will spend against the core quota. Pure.

    The baseline against GET /rate_limit is deliberately not counted: that
    endpoint answers without consuming any, which is what makes it usable as a
    control in a section full of notes about running out.
    """
    try:
        n, tries = len(paths or []), int(attempts)
    except (TypeError, ValueError):
        return 0
    return n * max(0, tries)


def timed_get(session, path, params, timeout):
    """One timed GET. Returns (status, elapsed, headers)."""
    started = time.monotonic()
    try:
        r = session.get(API + path, params=params, timeout=timeout)
    except requests.exceptions.RequestException:
        return None, time.monotonic() - started, {}
    return r.status_code, time.monotonic() - started, dict(r.headers)


def parse_params(pairs):
    """key=value strings into a dict. Pure."""
    out = {}
    for pair in pairs or []:
        if "=" in pair:
            key, value = pair.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", action="append", required=True,
                    help="the expensive API path, e.g. "
                         "/repos/o/n/compare/v1...main. Repeatable.")
    ap.add_argument("--param", action="append",
                    help="key=value query parameter. Repeatable.")
    ap.add_argument("--attempts", type=int, default=2,
                    help="timed attempts per path. Two is enough to tell a "
                         "repeatable cost problem from an incident.")
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="client timeout in seconds. Keep it above the "
                         "server's own budget or you will only ever measure "
                         "your own deadline.")
    ap.add_argument("--no-baseline", action="store_true",
                    help="skip the free GET /rate_limit control")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    params = parse_params(args.param)
    log.info("read cost: %d request(s) against the core hourly quota "
             "(the baseline is free)", read_cost(args.path, args.attempts))

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub rejects requests with no User-Agent outright.
        "User-Agent": UA,
    })

    baseline = None
    if not args.no_baseline:
        _s, baseline, _h = timed_get(session, "/rate_limit", {}, args.timeout)
        log.info("baseline: GET /rate_limit answered in %.2fs and consumed no "
                 "quota", baseline)

    findings = []
    for path in args.path:
        attempts = []
        for i in range(max(1, args.attempts)):
            status, elapsed, headers = timed_get(session, path, params, args.timeout)
            state, detail = classify(status, elapsed, headers)
            rid = request_id(headers)
            log.info("attempt %d: %s after %.1fs%s", i + 1, status, elapsed,
                     " (x-github-request-id %s)" % rid if rid else "")
            attempts.append({"status": status, "elapsed": round(elapsed, 2),
                             "request_id": rid, "state": state, "detail": detail})

        worst = attempts[0]
        for a in attempts:
            if retry_repeats_it(a["state"]):
                worst = a
                break
        log.info("%s: %s", worst["state"], worst["detail"])
        log.info("repair: %s", repair(worst["state"], params))
        if worst["state"] in ("timeout", "slow-success"):
            log.info("try instead: %s",
                     ", ".join("%s=%s" % kv for kv in sorted(narrow(params).items())))

        findings.append({
            "path": path,
            "baseline_seconds": round(baseline, 3) if baseline is not None else None,
            "attempts": attempts,
            "state": worst["state"],
            "detail": worst["detail"],
            "retry_reproduces_it": retry_repeats_it(worst["state"]),
            "retries_wasted_on_three": wasted_retries(worst["state"], 3),
            "narrowed_params": narrow(params),
            "repair": repair(worst["state"], params),
        })

    print(json.dumps({"requests_spent": read_cost(args.path, args.attempts),
                      "findings": findings}, indent=2, default=str))
    bad = {"timeout", "client-timeout", "slow-success"}
    return 1 if any(f["state"] in bad for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
