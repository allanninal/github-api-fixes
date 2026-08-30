from github_permission_upgrade_lag import (
    classify, cohorts, event_gap, permission_gap, permission_surplus, rank,
    verdict,
)

DECLARED = {"contents": "read", "issues": "write", "checks": "write"}
EVENTS = ["push", "issues", "check_run"]


def install(ident, login, permissions, events=None):
    return {"id": ident, "account": {"login": login},
            "permissions": permissions, "events": events or EVENTS}


def test_levels_are_ordered_and_absent_is_the_bottom():
    assert rank("admin") > rank("write") > rank("read") > rank(None)
    assert rank("READ ") == rank("read")


def test_an_unrecognised_level_is_treated_as_no_access():
    assert rank("superuser") == 0


def test_a_present_key_at_too_low_a_level_is_still_a_gap():
    gaps = permission_gap(DECLARED, {"contents": "read", "issues": "read",
                                     "checks": "write"})
    assert gaps == [("issues", "write", "read")]


def test_a_missing_key_reports_as_absent_rather_than_as_a_level():
    gaps = permission_gap(DECLARED, {"contents": "read", "checks": "write"})
    assert gaps == [("issues", "write", "absent")]


def test_an_installation_that_matches_has_no_gap():
    assert permission_gap(DECLARED, dict(DECLARED)) == []


def test_holding_more_than_the_app_declares_is_its_own_finding():
    extra = permission_surplus(DECLARED, {"contents": "write", "issues": "write",
                                          "checks": "write"})
    assert extra == [("contents", "read", "write")]
    undeclared = permission_surplus(DECLARED, dict(DECLARED, members="read"))
    assert undeclared == [("members", "not declared", "read")]


def test_events_are_compared_case_and_space_insensitively():
    assert event_gap(EVENTS, [" Push ", "issues", "check_run"]) == []
    assert event_gap(EVENTS, ["push"]) == ["check_run", "issues"]


def test_an_installation_behind_on_anything_is_upgrade_pending():
    row = classify(DECLARED, EVENTS,
                   install(1, "beta-inc", {"contents": "read", "checks": "write"}))
    assert row["state"] == "upgrade-pending"
    assert row["account"] == "beta-inc"
    assert row["permission_gap"] == [("issues", "write", "absent")]


def test_an_installation_behind_only_on_events_is_still_pending():
    row = classify(DECLARED, EVENTS, install(2, "acme", dict(DECLARED), ["push"]))
    assert row["state"] == "upgrade-pending"
    assert row["event_gap"] == ["check_run", "issues"]


def test_an_installation_that_agrees_is_current():
    assert classify(DECLARED, EVENTS, install(3, "acme", dict(DECLARED)))["state"] == "current"


def test_the_verdict_reports_pending_before_anything_else():
    rows = [classify(DECLARED, EVENTS, install(1, "a", dict(DECLARED))),
            classify(DECLARED, EVENTS, install(2, "b", {"contents": "read"}))]
    state, detail = verdict(rows)
    assert state == "upgrades-pending"
    assert "1 of 2" in detail


def test_a_fleet_that_is_only_ahead_is_not_an_outage():
    rows = [classify(DECLARED, EVENTS,
                     install(1, "a", dict(DECLARED, contents="write")))]
    state, detail = verdict(rows)
    assert state == "grants-ahead"
    assert "Nothing is failing" in detail


def test_an_app_with_no_installations_says_so_rather_than_all_current():
    assert verdict([])[0] == "no-installations"


def test_all_current_when_every_map_agrees():
    rows = [classify(DECLARED, EVENTS, install(i, str(i), dict(DECLARED)))
            for i in range(3)]
    assert verdict(rows)[0] == "all-current"


def test_accounts_missing_the_same_thing_collapse_into_one_cohort():
    rows = [classify(DECLARED, EVENTS, install(1, "beta", {"contents": "read", "checks": "write"})),
            classify(DECLARED, EVENTS, install(2, "gamma", {"contents": "read", "checks": "write"})),
            classify(DECLARED, EVENTS, install(3, "delta", dict(DECLARED)))]
    grouped = cohorts(rows)
    assert len(grouped) == 1
    assert list(grouped.values())[0] == ["beta", "gamma"]
