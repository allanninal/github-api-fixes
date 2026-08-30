from github_clock_skew import (
    GRACE, backdate_needed, best_sample, classify, classify_rate, drift_rate,
    interpret, parse_http_date, sample_skew, timezone_suspect,
)

NOW = 1_772_000_000.0


def test_a_date_header_parses_to_epoch_seconds():
    assert parse_http_date("Thu, 01 Jan 1970 00:00:10 GMT") == 10.0
    assert parse_http_date("not a date") is None
    assert parse_http_date("") is None
    assert parse_http_date(None) is None


def test_one_exchange_becomes_an_offset_with_an_error_bar():
    s = sample_skew(NOW, NOW + 40.0, NOW + 40.4)
    assert s["skew"] == 40.2
    assert s["uncertainty"] == 1.2
    assert s["round_trip"] == 0.4


def test_a_response_without_a_date_produces_no_sample():
    assert sample_skew(None, NOW, NOW + 0.2) is None


def test_the_fastest_exchange_wins_rather_than_the_average():
    slow = sample_skew(NOW, NOW + 40.0, NOW + 44.0)
    quick = sample_skew(NOW, NOW + 40.0, NOW + 40.1)
    assert best_sample([slow, quick, None])["round_trip"] == 0.1
    assert best_sample([]) is None
    assert best_sample([None]) is None


def test_a_host_running_fast_with_no_backdate_is_the_headline_finding():
    state, detail = classify(40.0, 1.0, 0)
    assert state == "iat-lands-in-the-future"
    assert "41.0s into GitHub" in detail


def test_the_same_offset_is_harmless_once_iat_is_backdated():
    state, detail = classify(40.0, 1.0, 60)
    assert state == "drift-absorbed-by-backdate"
    assert "19.0s to spare" in detail


def test_a_backdate_that_only_just_covers_the_drift_is_still_flagged():
    assert classify(58.0, 1.0, 60)[0] == "backdate-has-no-headroom"


def test_a_slow_path_cannot_resolve_a_small_offset():
    # Three seconds of skew measured over a six second round trip is inside
    # the error bar, so the honest answer is that the clocks agree.
    assert classify(3.0, 4.0, 60)[0] == "clock-in-sync"


def test_a_clock_behind_github_is_its_own_state_and_its_own_consequence():
    state, detail = classify(-45.0, 1.0, 60)
    assert state == "clock-behind-github"
    assert "already spent 45.0s" in detail


def test_whole_hours_are_a_timezone_and_not_drift():
    assert timezone_suspect(-18000.0) == -5.0
    assert timezone_suspect(19800.0) == 5.5
    assert timezone_suspect(41.0) is None
    assert timezone_suspect(2400.0) is None
    state, detail = classify(18000.0, 1.0, 60)
    assert state == "timezone-not-drift"
    assert "naive local datetime" in detail


def test_the_backdate_recommendation_covers_the_offset_and_its_error_bar():
    assert backdate_needed(5.0, 1.0) == 60
    assert backdate_needed(200.0, 2.0) == 210
    assert backdate_needed(-30.0, 1.0) == 60


def test_a_rate_is_refused_when_the_samples_are_too_close_together():
    readings = [(NOW, 10.0), (NOW + 4, 10.4)]
    assert drift_rate(readings) is None
    state, detail = classify_rate(None)
    assert state == "rate-not-measurable"
    assert "60s" in detail


def test_a_growing_offset_is_reported_as_a_free_running_clock():
    readings = [(NOW, 10.0), (NOW + 100, 10.05)]
    ppm = drift_rate(readings)
    assert ppm == 500.0
    state, detail = classify_rate(ppm)
    assert state == "clock-is-running-free"
    assert "43.2 seconds a day" in detail


def test_a_static_offset_says_the_clock_was_set_wrong_once():
    readings = [(NOW, 40.0), (NOW + 600, 40.0)]
    assert classify_rate(drift_rate(readings))[0] == "offset-is-static"


def test_an_unmeasurable_clock_says_so_rather_than_guessing():
    state, _ = classify(None, 1.0, 60)
    assert state == "unmeasurable"


def test_the_live_messages_separate_iat_from_its_neighbours():
    assert interpret(200, None)[0] == "accepted"
    assert interpret(401, "'Issued at' claim ('iat') must be an Integer "
                          "representing the time that the assertion was "
                          "issued")[0] == "github-refused-iat"
    assert interpret(401, "'Expiration time' claim ('exp') is too far in the "
                          "future")[0] == "lifetime-not-drift"
    assert interpret(401, "A JSON web token could not be "
                          "decoded")[0] == "key-or-encoding"
    assert interpret(404, "Integration not found")[0] == "issuer-does-not-resolve"
    assert interpret(403, "Resource not accessible by integration")[0] == "unrelated"


def test_the_grace_band_is_the_one_place_a_number_is_shared():
    assert GRACE == 5
