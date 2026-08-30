from github_graphql_timeout import (
    NEAR_LIMIT, POINTS_PER_QUERY, TIMEOUT_SECONDS, bucket_reading, charged,
    classify, headroom, looks_like_timeout, net_charge, operations, penalty,
    point_cost, refusal, repair, retry_projection, timeout_message,
    timing_consistent,
)

PAYLOAD = {"resources": {
    "core": {"limit": 5000, "used": 900, "remaining": 4100, "reset": 1780000000},
    "graphql": {"limit": 5000, "used": 1204, "remaining": 3796, "reset": 1780000000},
}}

TIMED_OUT = {"errors": [{"message": "Something went wrong while executing your "
                                    "query. This may be the result of a timeout"}]}
BEFORE = {"limit": 5000, "used": 1204, "remaining": 3796, "reset": 1780000000}
AFTER = {"limit": 5000, "used": 1225, "remaining": 3775, "reset": 1780000000}


def test_the_reading_comes_from_the_graphql_bucket_and_not_core():
    reading = bucket_reading(PAYLOAD)
    assert reading["used"] == 1204
    assert bucket_reading(PAYLOAD, "core")["used"] == 900
    assert bucket_reading({}) is None
    assert bucket_reading(None) is None


def test_the_charge_is_a_subtraction_over_one_window():
    assert charged(BEFORE, AFTER) == (21, "measured")
    assert charged(BEFORE, BEFORE) == (0, "measured")


def test_a_window_that_reset_between_readings_voids_the_measurement():
    rolled = dict(AFTER, reset=1780003600, used=3)
    assert charged(BEFORE, rolled) == (None, "window-reset")
    backwards = dict(AFTER, used=1100)
    assert charged(BEFORE, backwards) == (None, "window-reset")
    assert charged(BEFORE, None) == (None, "unreadable")
    assert charged({"used": "many", "reset": 1}, {"used": 2, "reset": 1}) == (None, "unreadable")


def test_a_known_background_drain_is_subtracted_rather_than_ignored():
    assert net_charge(21, 0) == 21
    assert net_charge(21, 5) == 16
    assert net_charge(3, 9) == 0
    assert net_charge(None, 0) is None


def test_a_timeout_is_recognised_by_status_or_by_message():
    assert looks_like_timeout(502, None)
    assert looks_like_timeout(504, None)
    assert looks_like_timeout(200, TIMED_OUT)
    assert not looks_like_timeout(200, {"data": {"viewer": {"login": "x"}}})
    assert timeout_message(TIMED_OUT).startswith("Something went wrong")
    assert timeout_message({"message": "Bad gateway"}) == "Bad gateway"
    assert timeout_message(None) is None


def test_the_clock_is_checked_against_the_documented_cutoff():
    assert TIMEOUT_SECONDS == 10
    assert timing_consistent(10.4)
    assert timing_consistent(8.0)
    assert not timing_consistent(3.2)
    assert not timing_consistent(None)
    assert round(headroom(8.0), 2) == 0.8
    assert headroom(-1) is None


def test_a_timeout_charged_above_its_normal_cost_is_the_headline():
    state, detail = classify(502, 10.4, 21, 12, 0, None)
    assert state == "timed-out-and-charged-extra"
    assert "penalty of 9 point(s)" in detail
    assert penalty(21, 12) == 9
    assert "Do not retry" in repair(state)


def test_a_timeout_that_did_not_prove_the_penalty_says_so():
    state, detail = classify(504, 10.1, 12, 12, 0, None)
    assert state == "timed-out-charge-not-proved"
    assert "not demonstrated by this run" in detail
    assert "smaller anyway" in repair(state)


def test_a_bucket_that_was_already_draining_makes_the_charge_unattributable():
    state, detail = classify(502, 10.2, 40, 12, 7, None)
    assert state == "timed-out-charge-not-attributable"
    assert "belongs to more than this call" in detail
    assert "its own token" in repair(state)


def test_an_unmeasurable_charge_is_never_reported_as_zero():
    state, detail = classify(502, 10.2, None, 12, 0, None)
    assert state == "charge-not-measurable"
    assert "did time out" in detail


def test_a_successful_call_near_the_cutoff_is_still_a_finding():
    state, detail = classify(200, 8.2, 12, 12, 0, {"data": {"x": 1}})
    assert state == "close-to-the-timeout"
    assert "82%" in detail
    assert NEAR_LIMIT == 0.7
    assert "rather than after the outage" in repair(state)


def test_an_ordinary_call_is_not_dressed_up_as_a_problem():
    state, detail = classify(200, 3.4, 12, 12, 0, {"data": {"x": 1}})
    assert state == "completed-inside-the-limit"
    assert "ordinary case" in detail


def test_the_retry_loop_is_priced_but_never_run():
    assert retry_projection(21, 3) == 63
    assert retry_projection(21, 0) == 0
    assert retry_projection(None, 3) == 0


def test_the_script_refuses_to_send_a_mutation():
    assert operations("query Q { viewer { login } }") == ["query"]
    assert refusal("mutation M { addStar(input: {}) { clientMutationId } }")
    assert refusal("subscription S { thing { id } }")
    assert refusal("query Q { viewer { login } }") is None


def test_the_run_says_what_it_will_spend_before_the_penalty():
    assert POINTS_PER_QUERY == 1
    assert point_cost(1) == 1
    assert point_cost(0) == 0
