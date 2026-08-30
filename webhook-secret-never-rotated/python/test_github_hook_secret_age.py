import json
from datetime import datetime, timezone

from github_hook_secret_age import (
    age_days, evidence_direction, parse_time, reconcile, redact, repair,
    secret_state, unedited_since_creation, verdict,
)

NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)
OLD = "2019-04-11T22:14:38Z"
RECENT = "2026-08-01T09:00:00Z"
MASKED = {"url": "https://hooks.example.com/github", "secret": "********",
          "content_type": "json"}
NO_SECRET = {"url": "https://hooks.example.com/github", "content_type": "json"}


def test_presence_is_the_only_fact_read_about_a_secret():
    assert secret_state(MASKED) == "set"
    assert secret_state(NO_SECRET) == "absent"
    assert secret_state(None) == "unknown"


def test_no_secret_value_survives_into_the_report():
    leaked = {"url": "https://hooks.example.com", "secret": "not-a-real-value"}
    printed = json.dumps(redact(leaked))
    assert "not-a-real-value" not in printed
    assert redact(leaked)["secret"] == "set"
    assert redact(MASKED)["secret"] == "set"
    assert "********" not in json.dumps(redact(MASKED))


def test_timestamps_are_parsed_and_aged_in_whole_days():
    assert parse_time(OLD).year == 2019
    assert parse_time("2019-04-11T22:14:38+00:00") == parse_time(OLD)
    assert parse_time("nonsense") is None
    assert parse_time(None) is None
    assert age_days(RECENT, NOW) == 29
    assert age_days(None, NOW) is None


def test_a_hook_never_edited_since_creation_is_recognised():
    assert unedited_since_creation(OLD, OLD)
    assert unedited_since_creation("2019-04-11T22:14:38Z", "2019-04-11T22:14:59Z")
    assert not unedited_since_creation(OLD, RECENT)
    assert not unedited_since_creation(None, RECENT)


def test_the_evidence_only_points_one_way():
    assert evidence_direction(2698, 180) == "conclusive"
    assert evidence_direction(29, 180) == "inconclusive"
    assert evidence_direction(180, 180) == "conclusive"
    assert evidence_direction(None, 180) == "unknown"


def test_an_ancient_hook_is_the_finding():
    state, detail = verdict(MASKED, OLD, OLD, NOW, 180)
    assert state == "overdue"
    assert "2698 days" in detail
    assert "the secret the hook was created with" in detail


def test_a_recent_edit_is_not_graded_as_compliant():
    state, detail = verdict(MASKED, OLD, RECENT, NOW, 180)
    assert state == "inconclusive"
    assert "an edit is not a rotation" in detail
    assert "unknown rather than compliant" in detail


def test_an_absent_secret_is_handed_to_the_other_note():
    state, detail = verdict(NO_SECRET, OLD, OLD, NOW, 180)
    assert state == "no-secret"
    assert "nothing to rotate" in detail
    assert "Age is not the problem" in repair("no-secret")


def test_a_claimed_rotation_the_hook_predates_is_a_finding():
    assert reconcile(OLD, "2026-02-14") == "not-applied"
    assert reconcile(RECENT, "2026-02-14") == "consistent"
    assert reconcile(RECENT, None) == "unknown"
    state, detail = verdict(MASKED, OLD, OLD, NOW, 180, claimed="2026-02-14")
    assert state == "rotation-not-applied"
    assert "2026-02-14" in detail
    assert "it was not this hook" in detail


def test_a_claim_the_hook_supports_does_not_override_the_age():
    state, _ = verdict(MASKED, OLD, RECENT, NOW, 180, claimed="2026-02-14")
    assert state == "inconclusive"


def test_an_unreadable_timestamp_is_admitted_rather_than_guessed():
    state, detail = verdict(MASKED, OLD, "not a date", NOW, 180)
    assert state == "age-unknown"
    assert "nothing about its age" in detail


def test_the_repair_never_suggests_a_straight_swap():
    assert "overlap window" in repair("overdue")
    assert "overlap window" in repair("rotation-not-applied")
    assert "written record" in repair("inconclusive")
