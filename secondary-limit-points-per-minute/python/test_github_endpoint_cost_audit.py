from github_endpoint_cost_audit import points_for, cost_profile, safe_rate, verdict


def test_reads_cost_one_point_and_writes_cost_five():
    assert points_for("GET") == 1
    assert points_for("head") == 1
    assert points_for("OPTIONS") == 1
    assert points_for("patch") == 5
    assert points_for("delete") == 5


def test_an_unknown_method_is_charged_the_expensive_rate():
    # Guessing low would produce a safe-looking ceiling for a request that is
    # not safe, and the ceiling is the number people pace against.
    assert points_for("QUERY") == 5
    assert points_for(None) == 5
    assert points_for("") == 5


def test_samples_are_grouped_by_path_and_averaged():
    profile = cost_profile([
        {"path": "/a", "method": "GET", "seconds": 0.1},
        {"path": "/a", "method": "GET", "seconds": 0.3},
        {"path": "/b", "method": "GET", "seconds": 1.0},
    ])
    assert profile["/a"]["calls"] == 2
    assert profile["/a"]["mean_seconds"] == 0.2
    assert profile["/a"]["max_seconds"] == 0.3
    assert profile["/b"]["mean_seconds"] == 1.0


def test_a_malformed_sample_is_dropped_rather_than_counted_as_instant():
    profile = cost_profile([
        {"path": "/a", "seconds": 0.5},
        {"path": "/a", "seconds": "slow"},
        {"seconds": 0.5},
        {"path": "/a", "seconds": -1},
    ])
    assert profile["/a"]["calls"] == 1
    assert profile["/a"]["mean_seconds"] == 0.5


def test_no_samples_profile_nothing():
    assert cost_profile([]) == {}
    assert cost_profile(None) == {}


def test_a_fast_endpoint_is_bound_by_points():
    safe = safe_rate(0.04)
    assert safe["binding"] == "points"
    assert safe["per_minute"] == 900.0
    assert safe["by_cpu"] == 2250.0


def test_a_slow_endpoint_is_bound_by_cpu_time_instead():
    safe = safe_rate(0.6)
    assert safe["binding"] == "cpu"
    assert safe["per_minute"] == 150.0


def test_the_two_ceilings_cross_at_a_tenth_of_a_second():
    assert safe_rate(0.09)["binding"] == "points"
    assert safe_rate(0.11)["binding"] == "cpu"


def test_a_very_expensive_endpoint_collapses_to_a_handful_a_minute():
    assert safe_rate(3.0)["per_minute"] == 30.0


def test_a_write_costs_five_points_so_its_ceiling_is_a_fifth():
    assert safe_rate(0.01, points=5)["per_minute"] == 180.0


def test_a_zero_response_time_does_not_divide_by_zero():
    safe = safe_rate(0.0)
    assert safe["by_cpu"] is None
    assert safe["binding"] == "points"
    assert safe_rate("unmeasured")["per_minute"] == 900.0


def test_with_no_configured_rate_the_ceiling_is_simply_reported():
    safe = safe_rate(0.5)
    state, detail = verdict("/x", {}, safe)
    assert state == "ceiling"
    assert "180" in detail


def test_a_rate_above_the_ceiling_names_the_cap_that_binds():
    safe = safe_rate(0.6)
    state, detail = verdict("/x", {"max_seconds": 0.9}, safe, configured=400)
    assert state == "over-budget"
    assert "CPU" in detail


def test_a_rate_just_under_the_ceiling_is_not_reported_as_fine():
    safe = safe_rate(0.6)  # 150 a minute
    state, detail = verdict("/x", {"max_seconds": 0.9}, safe, configured=130)
    assert state == "near-budget"
    assert "0.900 s" in detail


def test_an_expensive_path_is_flagged_even_at_a_low_rate():
    safe = safe_rate(2.0)  # 45 a minute
    state, detail = verdict("/x", {"max_seconds": 2.4}, safe, configured=5)
    assert state == "expensive"
    assert "move work off" in detail


def test_a_cheap_path_at_a_modest_rate_is_clear():
    state, detail = verdict("/x", {"max_seconds": 0.05}, safe_rate(0.04), configured=60)
    assert state == "clear"
    assert "900-points" in detail
