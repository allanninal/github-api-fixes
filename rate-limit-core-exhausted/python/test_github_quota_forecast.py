from github_quota_forecast import window_burn, sample_burn, verdict

NOW = 1_800_000_000.0


def at_minute(minute, used, limit=5000):
    """A window that opened `minute` minutes ago with `used` spent."""
    reset = NOW + (3600 - minute * 60)
    return window_burn(used, limit, reset, NOW)


def test_used_alone_says_nothing_until_it_is_a_rate():
    early = at_minute(5, 2400)
    late = at_minute(50, 2400)
    assert early["per_min"] > late["per_min"] * 5
    assert early["remaining"] == late["remaining"] == 2600


def test_a_steady_drain_that_fits_leaves_the_window_intact():
    win = at_minute(30, 1500)
    assert win["per_min"] == 50.0
    assert win["affordable"] == round(3500 / 30.0, 2)
    assert win["empty_in"] is None


def test_a_drain_that_does_not_fit_names_the_minute():
    win = at_minute(30, 4000)
    assert win["per_min"] > win["affordable"]
    assert win["empty_in"] is not None
    assert 400 < win["empty_in"] < 500


def test_an_empty_bucket_empties_in_zero_seconds():
    win = at_minute(45, 5000)
    assert win["remaining"] == 0
    assert win["empty_in"] == 0.0


def test_the_first_minute_does_not_divide_by_zero():
    win = window_burn(3, 5000, NOW + 3600, NOW)
    assert win["elapsed"] == 1.0
    assert win["per_min"] == 180.0


def test_a_reset_beyond_the_window_is_clamped_rather_than_trusted():
    # A skewed clock. Clamping makes elapsed small and the drain look high,
    # which is the safe direction to be wrong in.
    win = window_burn(100, 5000, NOW + 9000, NOW)
    assert win["left"] == 3600.0
    assert win["elapsed"] == 1.0


def test_unusable_numbers_return_nothing_rather_than_a_guess():
    assert window_burn(None, 5000, NOW, NOW) is None
    assert window_burn("many", 5000, NOW, NOW) is None


def test_two_samples_measure_the_drain_right_now():
    first = {"used": 1000, "reset": NOW + 1800, "at": NOW}
    second = {"used": 1030, "reset": NOW + 1800, "at": NOW + 30}
    assert sample_burn(first, second) == ("measured", 60.0)


def test_a_rolled_window_is_a_refill_not_a_negative_drain():
    first = {"used": 4900, "reset": NOW + 10, "at": NOW}
    second = {"used": 12, "reset": NOW + 3610, "at": NOW + 30}
    assert sample_burn(first, second) == ("rolled", None)


def test_one_sample_is_reported_as_one_sample():
    assert sample_burn({"used": 1, "reset": NOW, "at": NOW}, None) == ("single", None)
    assert sample_burn(None, None)[0] == "single"


def test_two_samples_at_the_same_instant_measure_nothing():
    s = {"used": 10, "reset": NOW + 60, "at": NOW}
    assert sample_burn(s, dict(s, used=20)) == ("no-gap", None)


def test_exhausted_reports_the_wait_and_refuses_to_call_it_a_fix():
    state, detail = verdict(at_minute(45, 5000))
    assert state == "exhausted"
    assert "900 second(s)" in detail
    assert "Waiting is not the repair" in detail


def test_a_measured_spike_overrides_a_comfortable_average():
    win = at_minute(50, 1000)  # a 20/min average with 4,000 still in the bucket
    state, detail = verdict(win, ("measured", 600.0))
    assert state == "will-exhaust"
    assert "measured over the sample gap" in detail


def test_a_burst_that_still_fits_is_flagged_as_spiky_not_safe():
    win = at_minute(10, 200)  # 20/min average, 4800 left over 50 minutes
    state, _ = verdict(win, ("measured", 60.0))
    assert state == "spiky"


def test_eighty_percent_used_is_tight_even_when_the_drain_fits():
    win = at_minute(55, 4100)
    state, detail = verdict(win, ("measured", 1.0))
    assert state == "tight"
    assert "second consumer" in detail


def test_a_healthy_window_is_clear():
    state, detail = verdict(at_minute(30, 900), ("measured", 30.0))
    assert state == "clear"
    assert "4100 left" in detail


def test_an_unreadable_body_is_not_reported_as_healthy():
    assert verdict(None)[0] == "unreadable"
