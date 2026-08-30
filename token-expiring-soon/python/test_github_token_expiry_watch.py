from github_token_expiry_watch import (
    bucket, header_value, parse_expiry, reading, schedule, seconds_left, verdict,
)

NOON = 1790769600  # 2026-09-30 12:00:00 UTC


def test_the_documented_shape_parses_to_the_right_instant():
    assert parse_expiry("2026-09-30 12:00:00 UTC") == NOON


def test_the_iso_shapes_parse_to_the_same_instant():
    assert parse_expiry("2026-09-30T12:00:00Z") == NOON
    assert parse_expiry("2026-09-30T12:00:00+00:00") == NOON


def test_a_numeric_offset_is_honoured_rather_than_ignored():
    assert parse_expiry("2026-09-30 07:00:00 -0500") == NOON
    assert parse_expiry("2026-09-30 14:00:00 +02:00") == NOON


def test_a_bare_date_is_read_as_midnight_utc():
    assert parse_expiry("2026-09-30") == NOON - 12 * 3600


def test_a_shape_that_is_not_recognised_returns_nothing_at_all():
    assert parse_expiry("soon") is None
    assert parse_expiry("30/09/2026") is None
    assert parse_expiry("") is None
    assert parse_expiry(None) is None


def test_the_header_is_found_whatever_its_case():
    assert header_value({"Github-Authentication-Token-Expiration": "x"}) == "x"
    assert header_value({"github-authentication-token-expiration": "y"}) == "y"
    assert header_value({"etag": "z"}) is None
    assert header_value(None) is None


def test_remaining_time_is_a_number_or_nothing():
    assert seconds_left(NOON, NOON - 60) == 60
    assert seconds_left(None, NOON) is None
    assert seconds_left(NOON, "later") is None


def test_the_thresholds_bucket_as_advertised():
    assert bucket(None) == "unknown"
    assert bucket(0) == "expired"
    assert bucket(-1) == "expired"
    assert bucket(3600) == "short-lived"
    assert bucket(2 * 86400) == "critical"
    assert bucket(10 * 86400) == "warning"
    assert bucket(20 * 86400) == "notice"
    assert bucket(90 * 86400) == "ok"


def test_custom_thresholds_are_respected():
    assert bucket(20 * 86400, thresholds=(60, 30, 21)) == "critical"


def test_a_successful_request_with_no_header_is_its_own_state():
    row = reading("GITHUB_TOKEN", 200, {"etag": "abc"}, NOON)
    assert row["state"] == "no-expiry-reported"
    assert "never expires or its class does not report one" in row["why"]


def test_a_failed_request_is_not_the_same_silence():
    assert reading("GITHUB_TOKEN", 500, {}, NOON)["state"] == "unreadable"
    assert reading("GITHUB_TOKEN", 0, {}, NOON)["state"] == "unreadable"


def test_a_refused_credential_has_no_forecast_left():
    assert reading("GITHUB_TOKEN", 401, {}, NOON)["state"] == "rejected"


def test_an_unparseable_header_is_reported_rather_than_guessed():
    row = reading("GITHUB_TOKEN", 200,
                  {"github-authentication-token-expiration": "next tuesday"}, NOON)
    assert row["state"] == "unreadable-header"
    assert "did not parse" in row["why"]


def test_a_live_reading_carries_the_remaining_seconds():
    headers = {"github-authentication-token-expiration": "2026-09-30 12:00:00 UTC"}
    row = reading("GITHUB_TOKEN", 200, headers, NOON - 2 * 86400)
    assert row["state"] == "critical"
    assert row["seconds_left"] == 2 * 86400
    assert row["expires_at"] == NOON


def test_an_unreadable_credential_outranks_a_healthy_one():
    rows = [{"name": "b", "state": "ok", "seconds_left": 90 * 86400},
            {"name": "a", "state": "unreadable", "seconds_left": None},
            {"name": "c", "state": "critical", "seconds_left": 2 * 86400}]
    assert [row["name"] for row in schedule(rows)] == ["c", "a", "b"]


def test_the_soonest_wins_inside_one_state():
    rows = [{"name": "later", "state": "warning", "seconds_left": 12 * 86400},
            {"name": "sooner", "state": "warning", "seconds_left": 8 * 86400}]
    assert schedule(rows)[0]["name"] == "sooner"


def test_the_verdict_is_the_top_row():
    ordered = schedule([{"name": "GITHUB_CI_TOKEN", "state": "critical",
                         "seconds_left": 2 * 86400}])
    state, detail = verdict(ordered)
    assert state == "critical"
    assert "2.0 day(s)" in detail
    assert "30, 14 and 3 days" in detail


def test_an_hour_left_is_reported_as_a_non_event():
    state, detail = verdict([{"name": "GITHUB_TOKEN", "state": "short-lived",
                              "seconds_left": 3540}])
    assert state == "short-lived"
    assert "59 minute(s)" in detail
    assert "does not distinguish them" in detail


def test_no_expiry_is_a_finding_and_not_a_clean_bill_of_health():
    state, detail = verdict([{"name": "GITHUB_BOT_TOKEN",
                              "state": "no-expiry-reported", "seconds_left": None}])
    assert state == "no-expiry-reported"
    assert "larger standing risk" in detail


def test_nothing_named_is_reported_as_nothing_checked():
    assert verdict([])[0] == "nothing-checked"
