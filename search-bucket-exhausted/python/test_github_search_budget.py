from github_search_budget import (bucket_pressure, plan_loop,
                                  pack_repo_queries, verdict)

NOW = 1_800_000_000.0

RESOURCES = {
    "core": {"limit": 5000, "used": 120, "remaining": 4880, "reset": NOW + 2400},
    "search": {"limit": 30, "used": 4, "remaining": 26, "reset": NOW + 41},
    "code_search": {"limit": 10, "used": 0, "remaining": 10, "reset": NOW + 55},
    "graphql": {"limit": 5000, "used": 0, "remaining": 5000, "reset": NOW + 2400},
}


def test_the_two_buckets_only_compare_once_the_windows_match():
    p = bucket_pressure(RESOURCES, NOW)
    assert p["core"]["per_minute"] == round(5000 / 60.0, 1)
    assert p["search"]["per_minute"] == 30.0
    # The whole point of the note: search is the tighter allowance even though
    # its limit is 166 times smaller.
    assert p["search"]["per_minute"] < p["core"]["per_minute"]


def test_code_search_is_tighter_still():
    p = bucket_pressure(RESOURCES, NOW)
    assert p["code_search"]["per_minute"] == 10.0


def test_a_bucket_with_an_unknown_window_is_reported_not_guessed():
    p = bucket_pressure({"something_new": {"limit": 99, "used": 1, "reset": NOW}}, NOW)
    assert p["something_new"]["per_minute"] is None
    assert p["something_new"]["limit"] == 99


def test_a_malformed_bucket_is_skipped():
    assert bucket_pressure({"core": {"limit": "lots"}}, NOW) == {}
    assert bucket_pressure(None, NOW) == {}


def test_refills_in_never_goes_negative():
    p = bucket_pressure({"search": {"limit": 30, "used": 30, "reset": NOW - 90}}, NOW)
    assert p["search"]["refills_in"] == 0


def test_a_loop_longer_than_the_window_refuses_the_surplus():
    plan = plan_loop(400, 30)
    assert plan["minutes"] == 13.3
    assert plan["refused_in_first_minute"] == 370


def test_a_loop_inside_the_window_refuses_nothing():
    assert plan_loop(12, 30)["refused_in_first_minute"] == 0


def test_a_missing_rate_is_not_treated_as_infinite():
    plan = plan_loop(400, None)
    assert plan["minutes"] is None
    assert plan["refused_in_first_minute"] is None


def test_a_short_list_becomes_one_query():
    packed = pack_repo_queries(["octo/one", "octo/two"], "is:issue is:open")
    assert len(packed["queries"]) == 1
    assert packed["queries"][0].startswith("is:issue is:open repo:octo/one")
    assert len(packed["queries"][0]) <= 256


def test_a_long_list_splits_and_every_query_fits():
    repos = ["acme/service-%02d" % i for i in range(40)]
    packed = pack_repo_queries(repos, "is:issue is:open label:bug")
    assert len(packed["queries"]) > 1
    assert all(len(q) <= 256 for q in packed["queries"])
    # Every repository appears exactly once across the packed queries.
    joined = " ".join(packed["queries"])
    assert all(joined.count("repo:" + r) == 1 for r in repos)
    # The saving is the point: 40 calls collapse to a handful.
    assert len(packed["queries"]) < 8


def test_a_repository_that_cannot_fit_beside_the_base_query_is_named():
    packed = pack_repo_queries(["acme/" + "x" * 250, "acme/ok"], "is:issue")
    assert packed["too_long"] == ["acme/" + "x" * 250]
    assert packed["queries"] == ["is:issue repo:acme/ok"]


def test_empty_input_packs_into_nothing():
    assert pack_repo_queries([], "is:issue")["queries"] == []
    assert pack_repo_queries(None)["queries"] == []
    assert pack_repo_queries(["", "  "])["queries"] == []


def test_boolean_operators_in_the_base_query_are_counted():
    packed = pack_repo_queries(["a/b"], "cat OR dog OR bird OR fish OR rat OR ox")
    assert packed["operators"] == 5
    assert packed["over_operator_limit"] is False
    more = pack_repo_queries(["a/b"], "a OR b OR c OR d OR e OR f OR g")
    assert more["over_operator_limit"] is True


def test_an_empty_search_bucket_points_at_the_healthy_core_one():
    p = bucket_pressure(dict(RESOURCES, search={"limit": 30, "used": 30,
                                                "remaining": 0, "reset": NOW + 12}), NOW)
    state, detail = verdict(p["search"], p["core"])
    assert state == "exhausted"
    assert "different buckets" in detail
    assert "12 second(s)" in detail


def test_an_oversized_loop_reports_the_packed_alternative():
    p = bucket_pressure(RESOURCES, NOW)
    repos = ["acme/service-%02d" % i for i in range(400)]
    state, detail = verdict(p["search"], p["core"],
                            plan_loop(400, p["search"]["per_minute"]),
                            pack_repo_queries(repos, "is:issue is:open"))
    assert state == "over-budget"
    assert "refused inside the first minute" in detail
    assert "queries" in detail


def test_the_core_comparison_is_stated_in_the_same_units():
    p = bucket_pressure(RESOURCES, NOW)
    _, detail = verdict(p["search"], p["core"])
    assert "83 a minute" in detail


def test_a_healthy_bucket_with_no_plan_is_clear():
    p = bucket_pressure(RESOURCES, NOW)
    assert verdict(p["search"], p["core"])[0] == "clear"


def test_no_search_bucket_is_not_reported_as_healthy():
    assert verdict(None, None)[0] == "no-search-bucket"
