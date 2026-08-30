from github_backoff_plan import (backoff, plan, required_wait,
                                 retry_after_seconds, wasted_requests)

NOW = 1756512000.0  # 2025-08-30T00:00:00Z


def test_retry_after_reads_the_integer_form():
    assert retry_after_seconds("120", NOW) == 120.0
    assert retry_after_seconds(" 60 ", NOW) == 60.0


def test_retry_after_reads_the_http_date_form_a_proxy_may_substitute():
    assert retry_after_seconds("Sat, 30 Aug 2025 00:02:00 GMT", NOW) == 120.0


def test_a_retry_after_already_in_the_past_is_zero_not_negative():
    assert retry_after_seconds("Fri, 29 Aug 2025 23:00:00 GMT", NOW) == 0.0


def test_an_unparseable_retry_after_is_absent_rather_than_zero():
    assert retry_after_seconds("soon", NOW) is None
    assert retry_after_seconds(None, NOW) is None
    assert retry_after_seconds("", NOW) is None


def test_retry_after_wins_over_the_reset_timestamp():
    # A secondary limit fires with the hourly bucket untouched, so the reset
    # timestamp is describing an hour that has nothing to do with this refusal.
    seconds, source, _ = required_wait(403, {
        "Retry-After": "120",
        "X-RateLimit-Remaining": "4870",
        "X-RateLimit-Reset": str(int(NOW + 3000)),
    }, NOW)
    assert source == "retry-after"
    assert seconds == 120.0


def test_an_empty_bucket_falls_through_to_the_reset_timestamp():
    seconds, source, detail = required_wait(403, {
        "x-ratelimit-remaining": "0",
        "x-ratelimit-reset": str(int(NOW + 1800)),
    }, NOW)
    assert source == "x-ratelimit-reset"
    assert seconds == 1800.0
    assert "hourly quota" in detail


def test_a_bucket_with_headroom_and_no_retry_after_uses_the_floor():
    seconds, source, _ = required_wait(429, {"x-ratelimit-remaining": "4900"}, NOW)
    assert source == "floor"
    assert seconds == 60.0


def test_a_response_that_is_not_throttled_asks_for_no_wait():
    seconds, source, _ = required_wait(200, {"retry-after": "120"}, NOW)
    assert source == "none"
    assert seconds == 0.0


def test_backoff_doubles_and_then_stops_at_the_cap():
    assert [backoff(i) for i in range(5)] == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert backoff(20) == 60.0
    assert backoff(-3) == 1.0


def test_wasted_requests_counts_what_fits_inside_the_wait():
    assert wasted_requests(120, 1) == 120
    assert wasted_requests(120, 30) == 4
    assert wasted_requests(0, 1) == 0


def test_wasted_requests_survives_a_nonsense_interval():
    assert wasted_requests(120, 0) == 0
    assert wasted_requests(120, -5) == 0


def test_a_one_second_retry_inside_a_two_minute_wait_is_hammering():
    state, report = plan(403, {"retry-after": "120"}, NOW, 1.0)
    assert state == "hammering"
    assert report["wasted_requests"] == 120
    assert report["source"] == "retry-after"


def test_a_client_that_waits_longer_than_asked_has_honoured_it():
    state, report = plan(403, {"retry-after": "120"}, NOW, 300.0)
    assert state == "honoured"
    assert report["wasted_requests"] == 0


def test_a_few_retries_inside_the_window_are_impatient_not_hammering():
    state, _ = plan(429, {"retry-after": "120"}, NOW, 30.0)
    assert state == "impatient"


def test_an_untroubled_response_reports_nothing_to_do():
    state, report = plan(200, {}, NOW, 1.0)
    assert state == "not-throttled"
    assert report["wait_seconds"] == 0.0
