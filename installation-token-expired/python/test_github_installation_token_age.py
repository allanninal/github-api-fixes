from github_installation_token_age import (
    DANGER_BAND, LIFETIME, cliff_at, classify, interpret, parse_expiry_header,
    parse_moment, reconcile, refresh_verdict, remaining,
)

NOW = 1_772_000_000.0

# Obviously not a credential.
FAKE = "tok"


def test_a_recorded_mint_time_parses_in_either_shape():
    assert parse_moment("1772000000") == NOW
    assert parse_moment("2026-02-25T08:33:20Z") == parse_moment("1772008400")
    assert parse_moment("") is None
    assert parse_moment(None) is None
    assert parse_moment("some time last tuesday") is None


def test_the_expiry_github_states_is_not_iso_8601():
    assert parse_expiry_header("2026-02-25 08:33:20 UTC") == parse_moment("1772008400")
    assert parse_expiry_header("") is None
    assert parse_expiry_header(None) is None


def test_the_remaining_life_names_the_source_it_came_from():
    left, source = remaining(NOW - 600, None, NOW)
    assert (left, source) == (3000, "record")
    left, source = remaining(NOW - 600, NOW + 120, NOW)
    assert (left, source) == (120, "github")
    assert remaining(None, None, NOW) == (None, "nothing")


def test_an_hour_old_token_is_the_headline_finding():
    state, detail = classify(-60)
    assert state == "expired"
    assert "60s ago" in detail
    assert "all at once" in detail


def test_the_bands_below_an_hour_are_named_separately():
    assert classify(3000)[0] == "fresh"
    assert classify(599)[0] == "past-the-safe-margin"
    assert classify(DANGER_BAND - 1)[0] == "inside-the-danger-band"
    assert classify(0)[0] == "expired"


def test_nothing_recorded_is_a_state_and_not_a_guess():
    state, detail = classify(None)
    assert state == "no-record"
    assert "Record the moment you mint" in detail


def test_minting_once_at_startup_is_found_before_it_fires():
    state, detail = refresh_verdict(0)
    assert state == "minted-once-at-startup"
    assert "60 minutes after start" in detail
    assert refresh_verdict(None)[0] == "minted-once-at-startup"


def test_an_hourly_timer_against_an_hourly_token_is_a_race():
    state, detail = refresh_verdict(LIFETIME)
    assert state == "refresh-slower-than-lifetime"
    assert "it is a race" in detail
    assert refresh_verdict(7200)[0] == "refresh-slower-than-lifetime"


def test_a_refresh_with_no_room_for_a_retry_is_still_flagged():
    assert refresh_verdict(3400)[0] == "refresh-without-margin"


def test_fifty_minutes_is_the_schedule_that_passes():
    state, detail = refresh_verdict(3000)
    assert state == "refresh-healthy"
    assert "600s of margin" in detail


def test_the_cliff_is_an_hour_after_the_mint():
    assert cliff_at(NOW) == int(NOW) + LIFETIME
    assert cliff_at(None) is None


def test_two_records_of_different_tokens_are_caught():
    state, detail = reconcile(NOW + 240, NOW + 1440)
    assert state == "record-disagrees"
    assert "1200s apart" in detail
    assert reconcile(NOW + 240, NOW + 250)[0] == "record-agrees"
    assert reconcile(None, NOW + 240)[0] == "no-header"
    assert reconcile(NOW + 240, None)[0] == "header-only"


def test_a_401_at_the_end_of_the_hour_is_the_expiry():
    state, detail = interpret(401, "Bad credentials", -30)
    assert state == "expired-as-predicted"
    assert "the arithmetic above" in detail


def test_a_401_with_most_of_the_hour_left_is_explicitly_not_this_problem():
    state, detail = interpret(401, "Bad credentials", 2400)
    assert state == "not-an-expiry-problem"
    assert "revoked, truncated or never valid" in detail


def test_a_401_with_no_record_refuses_to_choose():
    assert interpret(401, "Bad credentials", None)[0] == "expired-or-revoked-cannot-tell"


def test_the_other_responses_point_somewhere_else():
    assert interpret(200, None, 3000)[0] == "token-live"
    assert interpret(403, "Resource not accessible by integration",
                     3000)[0] == "wrong-credential-class"
    assert interpret(404, "Not Found", 3000)[0] == "route-not-answered"
    assert interpret(500, "Server Error", 3000)[0] == "unrelated"


def test_the_fixture_token_is_obviously_not_a_credential():
    assert len(FAKE) < 20
