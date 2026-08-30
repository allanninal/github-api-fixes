from github_feature_flags import (
    ENDPOINT_FEATURES, PLAN_DEPENDENT, classify, feature_for, flag_state,
    matrix, normalise_endpoint, plan_may_be_the_constraint, read_cost, repair,
    security_block, status_matches,
)

ADMIN_VIEW = {
    "private": True,
    "visibility": "private",
    "has_issues": False,
    "has_wiki": True,
    "security_and_analysis": {
        "advanced_security": {"status": "disabled"},
        "secret_scanning": {"status": "disabled"},
        "secret_scanning_push_protection": {"status": "disabled"},
        "dependabot_security_updates": {"status": "enabled"},
    },
}
# The same repository read by a collaborator without admin: no security block.
READER_VIEW = {"private": True, "visibility": "private", "has_issues": False,
               "has_wiki": True}
HEALTHY = {
    "private": False,
    "visibility": "public",
    "has_issues": True,
    "security_and_analysis": {
        "advanced_security": {"status": "enabled"},
        "secret_scanning": {"status": "enabled"},
    },
}


def test_one_off_switch_produces_three_status_codes():
    assert feature_for("/code-scanning/alerts")["status_when_disabled"] == 403
    assert feature_for("/secret-scanning/alerts")["status_when_disabled"] == 404
    assert feature_for("/issues")["status_when_disabled"] == 410
    # Only the first of those looks like a permissions problem.


def test_a_logged_url_is_reduced_to_a_table_key():
    assert normalise_endpoint(
        "https://api.github.com/repos/octo/pay/code-scanning/alerts?state=open"
    ) == "/code-scanning/alerts"
    assert normalise_endpoint("/repos/octo/pay/issues") == "/issues"
    assert normalise_endpoint("issues") == "/issues"
    assert normalise_endpoint("") == ""


def test_an_absent_security_block_is_unreported_and_never_disabled():
    assert security_block(READER_VIEW) is None
    assert flag_state(READER_VIEW, "advanced_security", "security") == "unreported"
    assert flag_state(ADMIN_VIEW, "advanced_security", "security") == "disabled"
    assert flag_state(ADMIN_VIEW, "dependabot_security_updates", "security") == "enabled"


def test_a_toggle_is_readable_by_anybody_who_can_read_the_repo():
    assert flag_state(READER_VIEW, "has_issues", "toggle") == "disabled"
    assert flag_state(READER_VIEW, "has_wiki", "toggle") == "enabled"
    assert flag_state(READER_VIEW, "has_discussions", "toggle") == "unreported"


def test_a_disabled_feature_is_named_and_no_permission_opens_it():
    row = feature_for("/code-scanning/alerts")
    state, detail = classify(ADMIN_VIEW, row, 403)
    assert state == "feature-disabled"
    assert "No permission opens it" in detail


def test_the_unreported_case_blames_the_readers_role_not_the_repo():
    row = feature_for("/code-scanning/alerts")
    state, detail = classify(READER_VIEW, row, 403)
    assert state == "feature-unreported"
    assert "admin on the repository" in detail
    assert "absent block is a limit on your reading" in repair(state, row)


def test_an_enabled_feature_with_a_named_permission_is_somebody_elses_note():
    row = feature_for("/code-scanning/alerts")
    state, detail = classify(HEALTHY, row, 403, "security_events=read")
    assert state == "permission-named"
    assert "security_events=read" in detail
    state, _ = classify(HEALTHY, row, 403, "")
    assert state == "feature-enabled"


def test_a_status_that_does_not_match_is_called_a_mismatch():
    row = feature_for("/secret-scanning/alerts")
    assert status_matches(row, 404) is True
    assert status_matches(row, 403) is False
    assert status_matches(row, None) is None
    state, detail = classify(ADMIN_VIEW, row, 403)
    assert state == "status-mismatch"
    assert "404" in detail and "403" in detail


def test_the_issues_toggle_answers_410_gone_which_reads_as_deprecation():
    row = feature_for("/issues")
    state, detail = classify(ADMIN_VIEW, row, 410)
    assert state == "feature-disabled"
    assert "410" in detail


def test_the_matrix_covers_every_endpoint_in_the_table():
    rows = matrix(ADMIN_VIEW)
    assert len(rows) == len(ENDPOINT_FEATURES)
    by_endpoint = {r["endpoint"]: r for r in rows}
    assert by_endpoint["/issues"]["will_serve"] is False
    assert by_endpoint["/dependabot/alerts"]["will_serve"] is True
    assert matrix(READER_VIEW)[0]["will_serve"] in (True, False, None)


def test_a_proxy_mapping_is_flagged_as_one():
    assert feature_for("/dependabot/alerts")["confidence"] == "proxy"
    assert feature_for("/secret-scanning/alerts")["confidence"] == "exact"
    row = feature_for("/dependabot/alerts")
    row["state"] = "disabled"
    assert "not proof" in repair("feature-disabled", row, ADMIN_VIEW)


def test_the_plan_can_be_a_repair_an_admin_cannot_make():
    assert "advanced_security" in PLAN_DEPENDENT
    assert plan_may_be_the_constraint(ADMIN_VIEW, "advanced_security") is True
    assert plan_may_be_the_constraint(HEALTHY, "advanced_security") is False
    assert plan_may_be_the_constraint(ADMIN_VIEW, "has_issues") is False
    row = feature_for("/code-scanning/alerts")
    assert "depends on the plan" in repair("feature-disabled", row, ADMIN_VIEW)


def test_an_endpoint_outside_the_table_is_handed_back():
    assert feature_for("/pulls") is None
    state, _ = classify(ADMIN_VIEW, None, 403)
    assert state == "endpoint-unknown"


def test_the_run_costs_one_read_plus_any_probes():
    assert read_cost() == 1
    assert read_cost(len(ENDPOINT_FEATURES)) == 1 + len(ENDPOINT_FEATURES)
