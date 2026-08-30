from github_etag_saving import measure, project, verdict

ETAG = 'W/"6c1a2f9e0b7d4a3c"'


def response(status, etag=ETAG, used=None):
    return {"status": status, "etag": etag, "used": used}


def test_a_304_that_did_not_move_the_counter_is_the_finding():
    state, report = measure(response(200, used=101), response(304, used=101))
    assert state == "free"
    assert report["cost_of_unchanged_poll"] == 0
    assert report["etag"] == ETAG


def test_an_endpoint_with_no_etag_cannot_be_polled_conditionally():
    state, _ = measure(response(200, etag=None, used=10), response(200, used=11))
    assert state == "no-etag"


def test_a_200_answer_to_a_conditional_request_is_its_own_finding():
    # A proxy that strips If-None-Match reinstates the full cost silently.
    state, report = measure(response(200, used=10), response(200, used=11))
    assert state == "not-honoured"
    assert report["cost_of_unchanged_poll"] == 1


def test_a_304_that_still_billed_is_reported_rather_than_smoothed_over():
    state, report = measure(response(200, used=10), response(304, used=12))
    assert state == "billed"
    assert report["cost_of_unchanged_poll"] == 2


def test_a_missing_used_header_leaves_the_saving_unmeasured():
    state, report = measure(response(200, used=None), response(304, used=None))
    assert state == "unmeasured"
    assert report["cost_of_unchanged_poll"] is None


def test_the_projection_prices_a_real_polling_schedule():
    p = project(30, 8, 5000, 1.0)
    assert p["per_hour_without"] == 960.0
    assert p["per_hour_with"] == 0.0
    assert p["saved_per_hour"] == 960.0
    assert p["percent_without"] == 19.2


def test_a_partly_changing_workload_saves_only_part_of_it():
    p = project(60, 1, 5000, 0.75)
    assert p["per_hour_without"] == 60.0
    assert p["per_hour_with"] == 15.0
    assert p["saved_per_hour"] == 45.0


def test_nothing_unchanged_means_nothing_saved():
    p = project(60, 1, 5000, 0.0)
    assert p["saved_per_hour"] == 0.0


def test_the_projection_refuses_nonsense_inputs_instead_of_dividing_by_zero():
    p = project(0, 0, 0, 5.0)
    assert p["limit"] == 1
    assert p["per_hour_without"] == 3600.0
    assert p["per_hour_with"] == 0.0


def test_a_large_share_of_quota_is_called_out_as_such():
    level, detail = verdict("free", project(30, 8, 5000, 1.0))
    assert level == "saving"
    assert "19.2%" in detail
    assert verdict("free", project(10, 8, 5000, 1.0))[0] == "large-saving"


def test_each_unhappy_state_names_a_different_repair():
    assert verdict("no-etag", project(60, 1))[0] == "unavailable"
    assert verdict("not-honoured", project(60, 1))[0] == "ignored"
    assert verdict("billed", project(60, 1))[0] == "billed"
    assert verdict("unmeasured", project(60, 1))[0] == "unmeasured"


def test_the_ignored_state_blames_the_header_not_the_quota():
    _, detail = verdict("not-honoured", project(60, 1))
    assert "If-None-Match" in detail


def test_the_billed_state_points_at_the_shared_counter():
    _, detail = verdict("billed", project(60, 1))
    assert "shares" in detail or "sharing" in detail
