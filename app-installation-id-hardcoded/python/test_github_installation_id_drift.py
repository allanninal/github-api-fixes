from github_installation_id_drift import (
    account_of, current_id_for, drift, index_by_account, index_by_id, parse_map,
    reinstalled_since, repair, stable_key, summarize, unmapped,
)

ACME = {"id": 55120044, "account": {"login": "acme-corp"},
        "created_at": "2026-08-25T08:02:11Z"}
GAMMA = {"id": 41234568, "account": {"login": "gamma-labs"},
         "created_at": "2024-02-01T00:00:00Z"}
BY_ID = index_by_id([ACME, GAMMA])
BY_ACCOUNT = index_by_account([ACME, GAMMA])


def test_the_map_is_read_as_pairs_or_as_json():
    assert parse_map("acme-corp=41234567,beta-inc=41234568") == {
        "acme-corp": "41234567", "beta-inc": "41234568"}
    assert parse_map(' {"Acme-Corp": 41234567} ') == {"acme-corp": "41234567"}
    assert parse_map(" acme-corp = 41234567 ; beta-inc=9 ") == {
        "acme-corp": "41234567", "beta-inc": "9"}
    assert parse_map("") == {}
    assert parse_map("nonsense") == {}
    assert parse_map("{not json") == {}


def test_ids_are_indexed_as_text_however_they_arrived():
    assert BY_ID["55120044"] is ACME
    assert index_by_id([{"id": "77", "account": {"login": "x"}}])["77"]["id"] == "77"
    assert index_by_id([{"account": {"login": "x"}}]) == {}


def test_the_stable_key_is_the_login_not_the_id():
    assert stable_key(ACME) == "acme-corp"
    assert stable_key({"id": 5, "account": {"login": "Acme-Corp"}}) == "acme-corp"
    assert stable_key({"id": 5}) is None
    assert account_of(None) is None


def test_an_id_that_belongs_to_another_account_is_its_own_finding():
    state, detail = drift("beta-inc", 41234568, BY_ID, BY_ACCOUNT)
    assert state == "crossed"
    assert "gamma-labs" in detail
    assert "wrong account" in detail
    assert "stop the deploy" in repair(state, "beta-inc")


def test_a_missing_id_on_a_live_account_names_the_current_one():
    state, detail = drift("acme-corp", 41234567, BY_ID, BY_ACCOUNT)
    assert state == "stale"
    assert "55120044" in detail
    assert current_id_for("ACME-CORP", BY_ACCOUNT) == "55120044"


def test_a_missing_id_on_a_missing_account_is_not_a_stale_id():
    state, detail = drift("delta-ltd", 999, BY_ID, BY_ACCOUNT)
    assert state == "gone"
    assert "no installation on that account" in detail


def test_a_matching_id_is_current_whether_it_was_stored_as_text():
    assert drift("acme-corp", "55120044", BY_ID, BY_ACCOUNT)[0] == "current"
    assert drift("Acme-Corp", 55120044, BY_ID, BY_ACCOUNT)[0] == "current"


def test_a_reinstall_after_the_map_was_written_is_flagged_even_when_it_matches():
    state, detail = drift("acme-corp", 55120044, BY_ID, BY_ACCOUNT,
                          recorded_at="2026-01-01T00:00:00Z")
    assert state == "current-but-reinstalled"
    assert "removed and re-added" in detail


def test_an_unreadable_date_is_a_third_answer_and_not_a_no():
    assert reinstalled_since(ACME, "2026-01-01T00:00:00Z") is True
    assert reinstalled_since(ACME, "2026-12-01T00:00:00Z") is False
    assert reinstalled_since(ACME, None) is None
    assert reinstalled_since({}, "2026-01-01T00:00:00Z") is None


def test_installations_the_map_never_mentions_are_listed_separately():
    assert unmapped(BY_ACCOUNT, {"acme-corp": "1"}) == ["gamma-labs"]
    assert unmapped(BY_ACCOUNT, {"ACME-CORP": "1", "gamma-labs": "2"}) == []


def test_the_summary_counts_the_silent_finding_apart():
    stats = summarize([{"state": "crossed"}, {"state": "stale"}, {"state": "current"}])
    assert stats["total"] == 3
    assert stats["silent"] == 1
    assert stats["by_state"]["stale"] == 1
