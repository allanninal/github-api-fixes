from datetime import datetime, timezone

from github_etag_credential_check import (
    classify_pair, fingerprint, rotation_waste, token_ttl, verdict)

NOON = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc).timestamp()


def test_a_304_that_becomes_a_200_under_another_credential_is_the_finding():
    state, detail = classify_pair(304, 200)
    assert state == "credential-scoped"
    assert "full price" in detail


def test_a_304_under_both_credentials_clears_rotation():
    assert classify_pair(304, 304)[0] == "shared"


def test_a_control_that_answers_200_is_not_a_rotation_result():
    state, detail = classify_pair(200, 200)
    assert state == "not-cacheable"
    assert "If-None-Match" in detail


def test_no_second_credential_is_unproven_rather_than_clear():
    state, detail = classify_pair(304, None)
    assert state == "unproven"
    assert "arithmetic, not a measurement" in detail


def test_a_second_credential_that_cannot_see_the_url_is_not_a_cache_finding():
    state, detail = classify_pair(304, 404)
    assert state == "inconclusive"
    assert "404" in detail


def test_a_control_that_did_not_complete_stops_the_analysis():
    assert classify_pair(None, 200)[0] == "inconclusive"
    assert classify_pair(500, 200)[0] == "inconclusive"


def test_an_hourly_token_rotates_twenty_four_times_a_day():
    waste = rotation_waste(40, 30, 3600)
    assert waste["rotations"] == 24
    assert waste["per_rotation"] == 40
    assert waste["daily"] == 960
    assert waste["polls"] == 115200


def test_a_credential_that_outlives_the_window_costs_nothing_inside_it():
    waste = rotation_waste(10, 60, 172800)
    assert waste["rotations"] == 0
    assert waste["daily"] == 0


def test_the_share_is_of_one_hours_quota_not_of_the_day():
    assert rotation_waste(2000, 60, 3600)["hourly_share"] == 0.4


def test_a_zero_interval_does_not_divide_by_zero():
    assert rotation_waste(5, 0, 0)["polls"] >= 0


def test_token_ttl_reads_the_z_suffix_github_actually_sends():
    assert token_ttl("2026-08-30T13:00:00Z", NOON) == 3600
    assert token_ttl("2026-08-30T13:00:00+00:00", NOON) == 3600


def test_an_expired_token_is_zero_and_an_unreadable_one_is_none():
    assert token_ttl("2026-08-30T11:00:00Z", NOON) == 0
    assert token_ttl("next tuesday", NOON) is None
    assert token_ttl(None, NOON) is None


def test_a_fleet_sized_cache_spends_a_quarter_of_an_hour_of_quota_per_mint():
    state, detail = verdict("credential-scoped", rotation_waste(2000, 60, 3600))
    assert state == "rotation-dominates"
    assert "40%" in detail


def test_a_small_cache_is_still_reported_as_a_cost():
    state, detail = verdict("credential-scoped", rotation_waste(40, 30, 3600))
    assert state == "rotation-costs"
    assert "960 a day" in detail


def test_nothing_is_projected_until_the_control_behaves():
    assert verdict("not-cacheable", rotation_waste(40, 30, 3600))[0] == "not-cacheable"
    assert verdict("shared", rotation_waste(40, 30, 3600))[0] == "shared"


def test_the_cache_key_is_a_digest_and_never_the_token():
    key = fingerprint("ghp_secretvalue")
    assert "ghp_secretvalue" not in key
    assert key == fingerprint("ghp_secretvalue")
    assert key != fingerprint("ghp_other")
