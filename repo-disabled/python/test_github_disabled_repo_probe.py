from github_disabled_repo_probe import (
    DEFAULT_PROBES, EMPTY_REPOSITORY, aggregate_impact, aggregate_safety,
    explains_subresource, is_disabled, is_real_zero, platform_state,
    probe_verdict, read_cost, remedy_owner, repair,
)

DISABLED = {"full_name": "acme/payments-legacy", "disabled": True, "archived": False}
ARCHIVED = {"full_name": "acme/legacy-billing", "disabled": False, "archived": True}
BOTH = {"full_name": "acme/gone", "disabled": True, "archived": True}
ACTIVE = {"full_name": "acme/platform-api", "disabled": False, "archived": False}

GHOST_PROBES = [{"path": "/branches", "status": 404},
                {"path": "/commits", "status": 404},
                {"path": "/contributors", "status": 404},
                {"path": "/languages", "status": 200}]
ALL_FINE = [{"path": "/branches", "status": 200},
            {"path": "/commits", "status": 200}]
NEW_REPO = [{"path": "/branches", "status": 200},
            {"path": "/commits", "status": EMPTY_REPOSITORY}]


def test_the_two_booleans_make_four_platform_states():
    assert platform_state(DISABLED) == "disabled"
    assert platform_state(ARCHIVED) == "archived"
    assert platform_state(BOTH) == "disabled-and-archived"
    assert platform_state(ACTIVE) == "active"
    assert platform_state(None) == "unknown"
    assert is_disabled("disabled-and-archived") is True
    assert is_disabled("archived") is False


def test_a_failure_is_only_explained_when_the_state_explains_it():
    assert explains_subresource("disabled", 404)[0] is True
    assert explains_subresource("disabled", 403)[0] is True
    assert explains_subresource("disabled", 200)[0] is True
    explained, why = explains_subresource("active", 404)
    assert explained is False
    assert "not explained by the repository state" in why


def test_archiving_does_not_explain_a_failed_read():
    explained, why = explains_subresource("archived", 404)
    assert explained is False
    assert "leaves reads working" in why


def test_an_empty_repository_is_never_reported_as_a_ghost():
    state, detail = probe_verdict("active", NEW_REPO)
    assert state == "empty-repository"
    assert "never been pushed to" in detail
    assert explains_subresource("disabled", EMPTY_REPOSITORY)[0] is False
    assert repair(state).startswith("nothing.")


def test_the_ghost_is_the_repository_object_reading_and_nothing_else_doing():
    state, detail = probe_verdict("disabled", GHOST_PROBES)
    assert state == "ghost-confirmed"
    assert "3 of 4 sub-resource(s)" in detail
    assert "billing or account matter" in repair(state)


def test_a_disabled_repository_that_answers_is_still_disabled():
    state, detail = probe_verdict("disabled", ALL_FINE)
    assert state == "disabled-but-answering"
    assert "Trust the boolean" in detail


def test_failures_without_a_state_to_explain_them_go_to_the_other_note():
    state, detail = probe_verdict("active", [{"path": "/branches", "status": 404}])
    assert state == "not-explained-by-state"
    assert "neither disabled nor archived" in detail
    assert "credential problem" in repair(state)


def test_archived_and_unreadable_are_handed_on_rather_than_absorbed():
    assert probe_verdict("archived", ALL_FINE)[0] == "archived-not-disabled"
    assert "repo-archived-writes-403" in repair("archived-not-disabled")
    assert probe_verdict("unknown", [])[0] == "repository-unreadable"
    assert probe_verdict("active", ALL_FINE)[0] == "healthy"


def test_the_aggregate_decision_is_the_output_that_matters():
    decision, reason = aggregate_safety("disabled")
    assert decision == "exclude"
    assert "artefact" in reason
    assert aggregate_safety("archived")[0] == "include"
    assert aggregate_safety("active")[0] == "include"
    assert aggregate_safety("unknown")[0] == "exclude"


def test_a_zero_from_a_disabled_repository_is_not_a_zero():
    assert is_real_zero("disabled", 0) is False
    assert is_real_zero("unknown", 0) is False
    assert is_real_zero("active", 0) is True
    assert is_real_zero("archived", 0) is True
    assert is_real_zero("disabled", 4) is None
    assert is_real_zero("active", None) is None


def test_the_sweep_reports_what_it_left_out():
    impact = aggregate_impact([{"state": "disabled"}, {"state": "active"},
                               {"state": "archived"}, {"state": "unknown"}])
    assert impact == {"counted": 2, "excluded": 2, "false_zeroes_avoided": 1}
    assert aggregate_impact([]) == {"counted": 0, "excluded": 0,
                                    "false_zeroes_avoided": 0}


def test_the_remedy_is_addressed_to_whoever_can_apply_it():
    assert "GitHub" in remedy_owner("disabled")
    assert "does not say which reason" in remedy_owner("disabled")
    assert "unarchiving" in remedy_owner("archived")
    assert remedy_owner("active") == "no remedy needed."


def test_the_cost_is_worked_out_before_anything_is_fetched():
    assert len(DEFAULT_PROBES) == 4
    assert read_cost(["a", "b"]) == 10
    assert read_cost(["a"], ("/languages",)) == 2
    assert read_cost([]) == 0
    assert read_cost(None) == 0
