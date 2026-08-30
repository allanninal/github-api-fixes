from github_graphql_points import (
    TIGHT, bucket, classify, fmt_reset, identify_budget, in_band_cost,
    is_rate_limited, operations, point_cost, queries_left, refusal, repair,
    seconds_between, seconds_to_reset, sustainable_rate, used_fraction,
)


def rl(core_remaining, graphql_remaining, core_limit=5000, graphql_limit=5000):
    return {"resources": {
        "core": {"limit": core_limit, "remaining": core_remaining,
                 "used": core_limit - core_remaining, "reset": 1_800_000_000},
        "graphql": {"limit": graphql_limit, "remaining": graphql_remaining,
                    "used": graphql_limit - graphql_remaining, "reset": 1_800_000_000},
    }}


def test_the_two_buckets_are_read_separately():
    body = rl(4983, 0)
    assert bucket(body, "core")["remaining"] == 4983
    assert bucket(body, "graphql")["remaining"] == 0
    assert bucket(body, "search") is None
    assert bucket({}, "graphql") is None


def test_an_empty_graphql_bucket_beside_a_healthy_core_is_the_headline():
    body = rl(4983, 0)
    state, detail = classify(bucket(body, "graphql"), bucket(body, "core"))
    assert state == "graphql-exhausted-rest-healthy"
    assert "health check reports green" in detail
    assert "resources.graphql.remaining" in repair(state)


def test_an_empty_core_beside_a_healthy_graphql_belongs_to_another_note():
    body = rl(0, 4983)
    state, _ = classify(bucket(body, "graphql"), bucket(body, "core"))
    assert state == "rest-exhausted-graphql-healthy"
    assert "rate-limit-core-exhausted" in repair(state)
    assert "point budgeting" in repair(state)


def test_both_empty_is_not_the_confusing_case():
    body = rl(0, 0)
    state, detail = classify(bucket(body, "graphql"), bucket(body, "core"))
    assert state == "both-exhausted"
    assert "not the confusing case" in detail


def test_a_tight_budget_is_flagged_before_it_reaches_zero():
    body = rl(4983, 500)
    state, _ = classify(bucket(body, "graphql"), bucket(body, "core"))
    assert state == "graphql-tight"
    assert TIGHT == 0.2
    healthy = rl(4983, 4000)
    assert classify(bucket(healthy, "graphql"), bucket(healthy, "core"))[0] == "both-healthy"


def test_a_missing_graphql_bucket_is_reported_rather_than_assumed_full():
    state, _ = classify(None, {"limit": 5000, "remaining": 4983})
    assert state == "unreadable"


def test_used_fraction_survives_a_bucket_that_makes_no_sense():
    assert used_fraction({"limit": 5000, "remaining": 2500}) == 0.5
    assert used_fraction({"limit": 0, "remaining": 0}) is None
    assert used_fraction({"limit": "many", "remaining": 1}) is None
    assert used_fraction(None) is None


def test_points_are_converted_into_the_unit_you_can_schedule():
    assert sustainable_rate(5000, 12) == 416
    assert seconds_between(5000, 12) == 8.7
    assert queries_left(1200, 12) == 100
    assert queries_left(11, 12) == 0


def test_the_conversion_refuses_a_cost_that_cannot_be_divided_by():
    assert sustainable_rate(5000, 0) is None
    assert queries_left(1200, 0) is None
    assert seconds_between(5000, "free") is None


def test_an_observed_limit_names_the_actor_it_belongs_to():
    assert identify_budget(5000) == "a user token"
    assert "GitHub Actions" in identify_budget(1000)
    assert "Enterprise Cloud" in identify_budget(10000)
    assert "matches none of the published budgets" in identify_budget(2500)


def test_the_error_type_is_read_from_the_envelope_not_the_status():
    assert is_rate_limited({"errors": [{"type": "RATE_LIMITED"}]})
    assert not is_rate_limited({"errors": [{"type": "NOT_FOUND"}]})
    assert not is_rate_limited({"data": {"rateLimit": {"cost": 1}}})
    assert not is_rate_limited(None)


def test_the_in_band_cost_is_read_only_when_it_was_asked_for():
    assert in_band_cost({"data": {"rateLimit": {"cost": 12, "remaining": 4988}}}) == 12
    assert in_band_cost({"data": {"viewer": {"login": "ada"}}}) is None
    assert in_band_cost({"data": None}) is None


def test_the_reset_delay_is_readable_and_never_negative():
    assert seconds_to_reset({"reset": 1000}, 940) == 60
    assert seconds_to_reset({"reset": 1000}, 2000) == 0
    assert seconds_to_reset({}, 100) is None
    assert fmt_reset(45) == "45s"
    assert fmt_reset(720) == "12m"
    assert fmt_reset(None) == "unknown"


def test_the_default_run_spends_nothing():
    assert point_cost(False) == 0
    assert point_cost(True) == 1


def test_the_script_refuses_to_send_a_mutation():
    assert operations("query { rateLimit { cost } }") == ["query"]
    assert refusal("mutation M { addStar(input: {}) { clientMutationId } }")
    assert refusal("query { rateLimit { cost remaining } }") is None
