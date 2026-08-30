import json
from datetime import datetime, timedelta, timezone

from github_sso_session_clock import (
    authorization_state, cadence_note, days_left, lapse_evidence, last_eight,
    match_authorization, parse_ts, read_cost, repair, token_kind,
    unattended_verdict,
)

NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)


def record(days, tail="fake1234", used="2026-08-31T04:11:07Z"):
    when = NOW + timedelta(days=days)
    return {
        "credential_id": 161195,
        "credential_type": "personal access token",
        "token_last_eight": tail,
        "credential_accessed_at": used,
        "authorized_credential_expires_at": when.isoformat().replace("+00:00", "Z"),
    }


def test_without_a_record_a_lapse_and_a_first_authorization_are_the_same():
    # The honest limit of this note. No admin credential means no record, and
    # the refusal alone cannot tell the two apart.
    state, detail = authorization_state(None, NOW, refused=True)
    assert state == "never-authorized"
    assert "first authorization rather than a lapse" in detail
    assert authorization_state(None, NOW, refused=False)[0] == "no-record-no-refusal"


def test_the_clock_produces_three_different_sentences():
    assert authorization_state(record(-1), NOW, True)[0] == "authorization-lapsed"
    assert authorization_state(record(3), NOW, False)[0] == "authorization-expiring"
    assert authorization_state(record(30), NOW, False)[0] == "authorization-active"


def test_the_lapsed_verdict_counts_the_days_it_has_been_dead():
    _state, detail = authorization_state(record(-4), NOW, True)
    assert "4 day(s) ago" in detail
    assert "credential is unchanged and valid" in detail


def test_the_expiring_verdict_is_a_forecast_with_a_number_on_it():
    _state, detail = authorization_state(record(3), NOW, False)
    assert "3 day(s)" in detail
    assert days_left(record(3)["authorized_credential_expires_at"], NOW) == 3
    assert days_left(record(-2)["authorized_credential_expires_at"], NOW) == -2


def test_a_record_with_no_expiry_is_not_reported_as_active():
    bare = {"token_last_eight": "fake1234", "credential_accessed_at": None}
    state, _ = authorization_state(bare, NOW, True)
    assert state == "expiry-not-published"


def test_the_match_runs_on_the_last_eight_and_nothing_else():
    tail = last_eight("ghp_fake1234")
    assert tail == "fake1234"
    records = [record(9, tail="other000"), record(2, tail=tail)]
    assert match_authorization(records, tail)["token_last_eight"] == tail
    assert match_authorization(records, "nomatch0") is None
    assert match_authorization(records, "") is None
    assert last_eight("short") == ""


def test_the_last_eight_never_reaches_the_report():
    # The characters identify a record. They are also eight characters of a
    # live credential, so nothing the script prints may contain them.
    tail = last_eight("ghp_fake1234")
    matched = match_authorization([record(5, tail=tail)], tail)
    state, detail = authorization_state(matched, NOW, False)
    report = json.dumps({
        "state": state,
        "detail": detail,
        "record_matched": bool(matched),
        "days_left": days_left(matched["authorized_credential_expires_at"], NOW),
        "repair": repair(state, "acme-corp", "classic PAT"),
        "cadence": cadence_note(state),
    })
    assert tail not in report


def test_past_use_is_what_proves_this_was_a_lapse():
    proven, detail = lapse_evidence(record(-1))
    assert proven is True and "did work against this organization" in detail
    assert lapse_evidence(None)[0] is False
    assert lapse_evidence(record(-1, used=None))[0] is False


def test_the_cadence_is_reported_as_inferred_not_measured():
    note = cadence_note("authorization-expiring")
    assert "not published" in note
    assert "will recur" in note
    assert cadence_note("no-record-no-refusal") == "nothing to forecast from this reading."


def test_an_installation_token_is_the_only_one_that_does_not_lapse():
    depends, detail = unattended_verdict("classic PAT")
    assert depends is True and "stops logging in" in detail
    depends, detail = unattended_verdict("App installation token")
    assert depends is False and "unattended work" in detail


def test_the_repair_renews_and_then_says_stop_renewing():
    fix = repair("authorization-expiring", "acme-corp", "classic PAT")
    assert "https://github.com/orgs/acme-corp/sso" in fix
    assert "does not and will not do it" in fix
    assert "App installation token" in fix
    # Nothing is offered for a credential that does not lapse with a person.
    assert "App installation token" not in repair(
        "authorization-expiring", "acme-corp", "App installation token")


def test_a_missing_record_sends_the_reader_to_the_sibling_note():
    fix = repair("never-authorized", "acme-corp", "classic PAT")
    assert "first time" in fix
    assert "does not recur" in fix


def test_timestamps_survive_both_spellings_of_utc():
    assert parse_ts("2026-09-03T09:22:41Z") == parse_ts("2026-09-03T09:22:41+00:00")
    assert parse_ts("not a date") is None
    assert parse_ts(None) is None


def test_the_credential_type_comes_from_its_prefix():
    assert token_kind("ghp_fake") == "classic PAT"
    assert token_kind("ghs_fake") == "App installation token"
    assert token_kind("nope") == "unknown"


def test_the_run_costs_two_reads_plus_the_record_pages():
    assert read_cost(False) == 2
    assert read_cost(True, 1) == 3
    assert read_cost(True, 3) == 5
