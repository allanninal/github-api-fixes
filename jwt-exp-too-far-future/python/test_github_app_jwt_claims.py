import base64
import json

from github_app_jwt_claims import (
    CEILING, audit, claims, decode_segment, interpret, lifetime, recommend, skew,
)

NOW = 1_772_000_000


def seg(value):
    raw = base64.urlsafe_b64encode(json.dumps(value).encode()).decode()
    return raw.rstrip("=")


def token(payload, header=None):
    """An obviously fake JWT: real claims, and the word sig for a signature."""
    return "%s.%s.sig" % (seg(header or {"alg": "RS256", "typ": "JWT"}),
                          seg(payload))


def test_a_segment_decodes_without_any_key():
    assert decode_segment(seg({"iss": "123456"})) == {"iss": "123456"}
    assert decode_segment("not base64 at all!!") is None
    assert decode_segment(seg([1, 2])) is None


def test_a_jwt_splits_into_a_header_and_a_payload():
    header, payload = claims(token({"iat": NOW, "exp": NOW + 540}))
    assert header["alg"] == "RS256"
    assert payload["exp"] - payload["iat"] == 540


def test_something_that_is_not_three_segments_decodes_to_nothing():
    assert claims("abc.def") == (None, None)
    assert claims("") == (None, None)
    assert claims(None) == (None, None)


def test_lifetime_and_skew_are_plain_arithmetic():
    payload = {"iat": NOW - 60, "exp": NOW + 480}
    assert lifetime(payload) == 540
    assert skew(payload, NOW) == -60
    assert lifetime({"iat": "2026-01-01", "exp": NOW}) is None
    assert skew({}, NOW) is None


def test_an_hour_long_jwt_is_the_headline_finding():
    state, detail = audit({"iat": NOW, "exp": NOW + 3600}, NOW)
    assert state == "exp-too-far-future"
    assert "3600s" in detail
    assert "3000s over" in detail


def test_the_ceiling_is_checked_before_the_clock():
    # Both faults present: an hour-long lifetime signed by a clock ten minutes
    # fast. The payload fault wins, because it is true whatever the time is.
    state, _ = audit({"iat": NOW + 600, "exp": NOW + 600 + 3600}, NOW)
    assert state == "exp-too-far-future"


def test_exactly_the_ceiling_is_still_legal():
    assert audit({"iat": NOW, "exp": NOW + CEILING}, NOW)[0] == "within-ceiling"
    assert audit({"iat": NOW, "exp": NOW + CEILING + 1}, NOW)[0] == "exp-too-far-future"


def test_a_missing_claim_is_named_rather_than_computed_around():
    assert audit({"exp": NOW + 300}, NOW)[0] == "no-iat"
    assert audit({"iat": NOW}, NOW)[0] == "no-exp"


def test_milliseconds_where_seconds_were_expected_are_caught():
    state, detail = audit({"iat": "1772000000", "exp": "1772000540"}, NOW)
    assert state == "non-numeric-claim"
    assert "millisecond" in detail


def test_exp_before_iat_is_its_own_state():
    state, detail = audit({"iat": NOW, "exp": NOW - 10}, NOW)
    assert state == "exp-not-after-iat"
    assert "10 second(s) before" in detail


def test_a_cached_jwt_that_ran_out_is_told_apart_from_a_long_one():
    state, detail = audit({"iat": NOW - 900, "exp": NOW - 360}, NOW)
    assert state == "already-expired"
    assert "cached" in detail


def test_a_fast_signing_clock_is_reported_as_drift_and_not_as_the_ceiling():
    state, detail = audit({"iat": NOW + 300, "exp": NOW + 540}, NOW)
    assert state == "iat-in-the-future"
    assert "different repair" in detail


def test_a_jwt_about_to_expire_is_flagged_before_it_does():
    assert audit({"iat": NOW - 580, "exp": NOW + 20}, NOW)[0] == "expiring-imminently"


def test_a_healthy_jwt_says_so_without_qualification():
    state, detail = audit({"iat": NOW - 60, "exp": NOW + 480}, NOW)
    assert state == "within-ceiling"
    assert "540s" in detail


def test_the_recommendation_is_a_pair_of_numbers_to_paste():
    want = recommend({"iat": NOW, "exp": NOW + 3600}, NOW)
    assert want["iat"] == NOW - 60
    assert want["exp"] == NOW + 480
    assert want["seconds_to_remove"] == 3060


def test_the_live_messages_map_to_the_same_states_as_the_local_check():
    assert interpret(200, None)[0] == "accepted"
    assert interpret(401, "'Expiration time' claim ('exp') is too far in the "
                          "future")[0] == "exp-too-far-future"
    assert interpret(401, "'Issued at' claim ('iat') is in the "
                          "future")[0] == "iat-in-the-future"
    assert interpret(401, "'Expiration time' claim ('exp') must be a numeric "
                          "value representing the future time"
                     )[0] == "already-expired"
    assert interpret(401, "A JSON web token could not be "
                          "decoded")[0] == "undecodable"
    assert interpret(404, "Integration not found")[0] == "wrong-app-or-key"
    assert interpret(403, "Resource not accessible by integration")[0] == "unrelated"
