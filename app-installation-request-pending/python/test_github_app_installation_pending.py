from datetime import datetime, timezone

from github_app_installation_pending import (
    ABSENCE_MEANING, actionable, age_days, installation_index, printed_step,
    probe_state, product_repair, read_cost, reconcile, request_age_state,
)

NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)

INSTALLATIONS = [
    {"id": 41, "account": {"login": "Initech"}, "created_at": "2026-07-02T09:00:00Z",
     "repository_selection": "all", "suspended_at": None},
    {"id": 42, "account": {"login": "umbrella"}, "created_at": "2026-05-01T09:00:00Z",
     "repository_selection": "selected", "suspended_at": "2026-08-01T09:00:00Z"},
]


def test_the_index_is_case_insensitive_because_records_are_hand_written():
    index = installation_index(INSTALLATIONS)
    assert index["initech"]["id"] == 41
    assert index["umbrella"]["suspended"] is True
    assert index["initech"]["suspended"] is False
    assert installation_index([{"id": 1}]) == {}


def test_the_probe_says_what_a_404_does_and_does_not_mean():
    state, detail = probe_state(404)
    assert state == "no-installation"
    assert ABSENCE_MEANING in detail
    assert probe_state(200)[0] == "installed"
    assert probe_state(401)[0] == "unreadable"


def test_a_product_that_says_connected_against_nothing_is_the_headline():
    state, detail = reconcile(
        {"account": "globex", "connected": True, "started_at": "2026-08-10T00:00:00Z"},
        None, NOW)
    assert state == "false-connected"
    assert "globex" in detail
    assert ABSENCE_MEANING in detail
    assert actionable(state) is True


def test_a_fresh_request_and_a_forgotten_one_are_different_sentences():
    fresh, detail = reconcile(
        {"account": "globex", "connected": False,
         "started_at": "2026-08-27T12:00:00Z"}, None, NOW)
    assert fresh == "awaiting-approval"
    assert "may simply not have looked yet" in detail
    stale, detail = reconcile(
        {"account": "globex", "connected": False,
         "started_at": "2026-07-01T12:00:00Z"}, None, NOW)
    assert stale == "stale-request"
    assert "notified once" in detail


def test_the_reconciliation_runs_in_the_other_direction_too():
    state, detail = reconcile({"account": "initech", "connected": False},
                              installation_index(INSTALLATIONS)["initech"], NOW)
    assert state == "unrecorded-installation"
    assert "nothing in your product noticed" in detail


def test_a_suspended_installation_is_handed_to_its_own_note():
    # Telling somebody to chase an approval that already happened is worse than
    # saying nothing, so this never counts as a pending request.
    state, detail = reconcile({"account": "umbrella", "connected": True},
                              installation_index(INSTALLATIONS)["umbrella"], NOW)
    assert state == "installed-but-suspended"
    assert "already happened" in detail
    assert "unsuspend" in printed_step(state, "umbrella")


def test_agreement_in_either_direction_is_quiet():
    assert reconcile({"account": "initech", "connected": True},
                     installation_index(INSTALLATIONS)["initech"], NOW)[0] == (
        "agreed-connected")
    assert reconcile({"account": "globex", "connected": False}, None, NOW)[0] == (
        "agreed-disconnected")
    assert actionable("agreed-connected") is False


def test_an_unaged_request_does_not_pretend_to_know_when_it_started():
    state, detail = request_age_state(None)
    assert state == "age-unknown"
    assert "does not say when the flow started" in detail
    assert age_days(None, NOW) is None
    assert age_days("not-a-date", NOW) is None
    assert round(age_days("2026-08-27T12:00:00Z", NOW), 1) == 2.0


def test_the_step_is_addressed_to_a_human_and_never_taken():
    step = printed_step("false-connected", "globex")
    assert "an owner of globex has to approve" in step
    assert "Nothing here requests or approves anything" in step
    assert printed_step("agreed-connected", "globex") == "nothing for this account."


def test_the_product_repair_is_about_the_state_machine_not_the_api():
    fix = product_repair(["false-connected", "agreed-connected"])
    assert "stop rendering a completed flow as a connection" in fix
    assert "on a schedule" in fix
    assert "expires by neglect" in product_repair(["stale-request"])
    assert product_repair(["agreed-connected"]).startswith("nothing")


def test_the_cost_counts_the_list_pages_and_one_probe_each():
    assert read_cost([{"account": "a"}, {"account": "b"}], 1) == 4
    assert read_cost([{"account": "a"}], 3) == 5
    assert read_cost([], 1) == 2
