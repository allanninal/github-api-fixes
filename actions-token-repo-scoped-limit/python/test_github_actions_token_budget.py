from github_actions_token_budget import classify, plan, pool_reset_in, verdict


def test_a_thousand_is_the_actions_token():
    klass, confidence, note = classify(1000)
    assert klass == "actions-token"
    assert confidence == "likely"
    assert "belongs to the repository" in note


def test_two_corroborating_signals_raise_the_confidence():
    klass, confidence, note = classify(1000, graphql_limit=1000, user_status=403)
    assert klass == "actions-token"
    assert confidence == "high"
    assert "403" in note and "1000 points" in note


def test_five_thousand_is_reported_as_ambiguous_rather_than_as_a_user():
    klass, confidence, _ = classify(5000)
    assert klass == "user-or-app"
    assert confidence == "ambiguous"


def test_the_scaled_and_enterprise_ceilings_are_separated():
    assert classify(15000)[0] == "enterprise-user"
    assert classify(12500)[0] == "app-installation"


def test_sixty_is_the_anonymous_tier_and_not_a_budget_problem():
    assert classify(60)[0] == "anonymous"
    assert verdict("anonymous", plan(4, 100))[0] == "unauthenticated"


def test_an_unreadable_ceiling_does_not_become_a_number():
    assert classify(None)[0] == "unknown"
    assert classify("plenty")[1] == "none"


def test_the_matrix_multiplies_the_job_count():
    costing = plan(jobs=4, calls_per_job=120, matrix_legs=3)
    assert costing["legs"] == 3
    assert costing["jobs"] == 12
    assert costing["total"] == 1440


def test_an_overrun_names_the_first_job_that_starves():
    costing = plan(jobs=12, calls_per_job=120, ceiling=1000)
    assert costing["fits"] is False
    assert costing["jobs_served"] == 8
    assert costing["first_starved_job"] == 9
    assert costing["shortfall"] == 440


def test_remaining_is_used_when_it_is_supplied_and_is_labelled():
    costing = plan(jobs=5, calls_per_job=100, remaining=240)
    assert costing["source"] == "remaining"
    assert costing["headroom"] == 240
    assert costing["first_starved_job"] == 3


def test_no_calls_is_not_a_division_by_zero():
    costing = plan(jobs=6, calls_per_job=0)
    assert costing["total"] == 0
    assert costing["fits"] is True
    assert costing["first_starved_job"] is None


def test_a_described_overrun_reads_as_a_job_number():
    state, detail = verdict("actions-token", plan(12, 120, ceiling=1000))
    assert state == "pool-overrun"
    assert "Job 9 of 12" in detail
    assert "whole repository shares" in detail


def test_four_fifths_of_the_pool_is_already_a_finding():
    state, detail = verdict("actions-token", plan(8, 100, ceiling=1000))
    assert state == "pool-tight"
    assert "four fifths" in detail


def test_a_run_well_inside_the_pool_is_reported_as_fitting():
    assert verdict("actions-token", plan(2, 50, ceiling=1000))[0] == "fits"


def test_costing_a_laptop_credential_says_so_rather_than_passing():
    state, detail = verdict("user-or-app", plan(12, 120, ceiling=5000))
    assert state == "different-ceiling"
    assert "from inside the job" in detail


def test_nothing_described_is_not_a_pass_either():
    assert verdict("actions-token", plan(0, 0))[0] == "no-workflow"


def test_the_reset_is_seconds_or_nothing():
    assert pool_reset_in(1000, 940) == 60
    assert pool_reset_in(900, 940) == 0
    assert pool_reset_in(None, 940) is None
