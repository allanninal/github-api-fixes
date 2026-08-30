from github_poll_interval_check import assess, floor_seconds, parse_max_age, verdict


def test_the_declared_interval_wins_and_is_named_as_the_source():
    seconds, source = floor_seconds({"X-Poll-Interval": "60"})
    assert seconds == 60
    assert source == "x-poll-interval"


def test_header_case_does_not_matter():
    assert floor_seconds({"x-poll-interval": "90"})[0] == 90


def test_cache_control_is_the_fallback_before_the_assumption():
    seconds, source = floor_seconds({"Cache-Control": "public, max-age=45, s-maxage=60"})
    assert seconds == 45
    assert source == "cache-control max-age"


def test_a_missing_header_is_labelled_as_an_assumption():
    seconds, source = floor_seconds({})
    assert seconds == 60
    assert source == "documented default"


def test_junk_and_zero_values_do_not_become_the_floor():
    assert floor_seconds({"x-poll-interval": "soon"})[1] == "documented default"
    assert floor_seconds({"x-poll-interval": "0"})[1] == "documented default"
    assert parse_max_age("max-age=0") is None
    assert parse_max_age(None) is None


def test_polling_under_the_floor_counts_the_requests_that_cannot_help():
    result = assess(5, 60, has_etag=False)
    assert result["state"] == "under-floor"
    assert result["polls_per_hour"] == 720
    assert result["allowed_per_hour"] == 60
    assert result["wasted_per_hour"] == 660
    assert result["billable_per_hour"] == 660


def test_an_etag_makes_the_same_extra_polls_free():
    result = assess(5, 60, has_etag=True)
    assert result["wasted_per_hour"] == 660
    assert result["billable_per_hour"] == 0


def test_the_floor_itself_is_at_the_floor():
    assert assess(60, 60, has_etag=True)["state"] == "at-floor"
    assert assess(75, 60, has_etag=True)["state"] == "at-floor"


def test_polling_far_slower_is_measured_in_staleness_not_requests():
    result = assess(600, 60, has_etag=True)
    assert result["state"] == "over-floor"
    assert result["wasted_per_hour"] == 0
    assert result["extra_staleness_s"] == 540


def test_a_zero_interval_is_clamped_rather_than_dividing_by_zero():
    assert assess(0, 60, has_etag=True)["polls_per_hour"] == 3600


def test_extra_polls_without_an_etag_are_a_quota_finding():
    state, detail = verdict(assess(5, 60, has_etag=False))
    assert state == "burning-quota"
    assert "660 request(s)" in detail


def test_extra_polls_with_an_etag_are_pointless_rather_than_expensive():
    state, detail = verdict(assess(5, 60, has_etag=True))
    assert state == "free-but-pointless"
    assert "cost no quota" in detail


def test_too_slow_is_reported_as_staleness():
    state, detail = verdict(assess(600, 60, has_etag=True))
    assert state == "slower-than-needed"
    assert "540s" in detail


def test_matching_the_floor_has_nothing_to_reclaim():
    state, detail = verdict(assess(60, 60, has_etag=True))
    assert state == "at-floor"
    assert "either direction" in detail
