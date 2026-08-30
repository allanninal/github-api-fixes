from github_concurrency_probe import classify, peak_overlap, verdict

SECONDARY = ('{"message":"You have exceeded a secondary rate limit. '
             'Please wait a few minutes before you try again."}')
PRIMARY = '{"message":"API rate limit exceeded for user ID 12345."}'
DENIED = '{"message":"Resource not accessible by integration"}'


def headers(remaining=4800, **extra):
    h = {"X-RateLimit-Limit": "5000", "X-RateLimit-Used": str(5000 - remaining)}
    if remaining is not None:
        h["X-RateLimit-Remaining"] = str(remaining)
    h.update(extra)
    return h


def test_a_secondary_limit_is_named_in_the_body():
    state, detail = classify(403, SECONDARY, headers(4800))
    assert state == "secondary"
    assert "4800" in detail


def test_the_same_message_on_a_429_classifies_identically():
    assert classify(429, SECONDARY, headers(4800))[0] == "secondary"


def test_an_empty_bucket_is_the_primary_quota_not_a_secondary_limit():
    state, detail = classify(403, PRIMARY, headers(0))
    assert state == "primary"
    assert "x-ratelimit-reset" in detail


def test_headroom_left_is_enough_to_suspect_a_secondary_limit():
    # The wording has changed before, so the fallback must not need it.
    state, detail = classify(403, '{"message":"Something new"}', headers(4321))
    assert state == "secondary-suspected"
    assert "4321" in detail


def test_a_403_with_no_rate_limit_headers_is_a_permissions_problem():
    state, _ = classify(403, DENIED, {})
    assert state == "forbidden"


def test_header_case_does_not_change_the_verdict():
    lower = {"x-ratelimit-remaining": "0"}
    assert classify(403, PRIMARY, lower)[0] == "primary"


def test_a_404_is_not_a_throttle():
    assert classify(404, '{"message":"Not Found"}', headers())[0] == "other"


def test_a_success_is_reported_with_its_headroom():
    state, detail = classify(200, "{}", headers(4999))
    assert state == "ok"
    assert "4999" in detail


def test_overlap_of_sequential_requests_is_one():
    assert peak_overlap([(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]) == 1


def test_overlap_counts_only_spans_open_at_the_same_instant():
    assert peak_overlap([(0.0, 3.0), (1.0, 2.0), (1.5, 4.0)]) == 3
    assert peak_overlap([(0.0, 1.0), (0.5, 2.0)]) == 2


def test_an_empty_probe_has_no_overlap():
    assert peak_overlap([]) == 0
    assert peak_overlap(None) == 0


def test_a_reversed_span_is_still_measured():
    assert peak_overlap([(2.0, 0.0), (1.0, 1.5)]) == 2


def test_any_throttled_response_beats_a_low_peak():
    state, detail = verdict(3, ["ok", "secondary", "ok"])
    assert state == "tripped"
    assert "1 of 3" in detail


def test_a_peak_at_the_ceiling_is_reported_even_when_nothing_failed():
    state, _ = verdict(100, ["ok"] * 100)
    assert state == "over-ceiling"
    assert verdict(85, ["ok"])[0] == "near-ceiling"


def test_clear_does_not_claim_the_client_is_safe():
    state, detail = verdict(6, ["ok", "ok"])
    assert state == "clear"
    assert "headroom API" in detail
