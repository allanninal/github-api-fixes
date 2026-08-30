from datetime import datetime, timezone

from github_installation_suspension import (
    account_of, days_since, find, is_suspended, repair, retryable,
    summarize, suspended_at, suspended_by, verdict,
)

NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)
LIVE = {"id": 41234567, "account": {"login": "acme-corp"}, "suspended_at": None}
DEAD = {"id": 41234568, "account": {"login": "beta-inc"},
        "suspended_at": "2026-08-27T09:14:22Z",
        "suspended_by": {"login": "octo-admin"}}


def test_every_shape_of_absent_timestamp_means_not_suspended():
    assert not is_suspended({"id": 1})
    assert not is_suspended({"id": 1, "suspended_at": None})
    assert not is_suspended({"id": 1, "suspended_at": ""})
    assert not is_suspended({"id": 1, "suspended_at": "   "})
    assert not is_suspended({"id": 1, "suspended_at": "null"})


def test_a_real_timestamp_is_a_suspension():
    assert is_suspended(DEAD)
    assert suspended_at(DEAD) == "2026-08-27T09:14:22Z"


def test_a_suspension_with_no_named_actor_is_still_a_suspension():
    anon = {"id": 9, "suspended_at": "2026-08-27T09:14:22Z", "suspended_by": None}
    assert is_suspended(anon)
    assert suspended_by(anon) is None
    assert suspended_by(DEAD) == "octo-admin"


def test_the_age_is_measured_from_the_timestamp():
    assert days_since("2026-08-27T09:14:22Z", NOW) == 3
    assert days_since("not a date", NOW) is None
    assert days_since(None, NOW) is None


def test_an_id_matches_whether_it_was_stored_as_text_or_a_number():
    assert find([LIVE, DEAD], 41234568) is DEAD
    assert find([LIVE, DEAD], "41234568") is DEAD
    assert find([LIVE, DEAD], " 41234568 ") is DEAD
    assert find([LIVE, DEAD], 999) is None


def test_the_summary_counts_both_sides():
    stats = summarize([LIVE, DEAD, {"id": 3}])
    assert stats == {"total": 3, "suspended": 1, "active": 2,
                     "suspended_ids": [41234568]}


def test_a_suspended_installation_names_the_moment_and_the_actor():
    state, detail = verdict(DEAD, None, NOW)
    assert state == "suspended"
    assert "octo-admin" in detail
    assert "3 day(s) ago" in detail
    assert not retryable(state)


def test_a_missing_id_is_never_reported_as_a_suspension():
    state, detail = verdict(None, 403, NOW)
    assert state == "not-listed"
    assert "different repair" in detail
    assert not retryable(state)


def test_a_403_on_an_active_installation_is_sent_elsewhere():
    state, detail = verdict(LIVE, 403, NOW)
    assert state == "active-but-refused"
    assert "rather than about suspension" in detail
    assert retryable(state)


def test_an_active_installation_with_no_probe_is_just_active():
    assert verdict(LIVE, None, NOW)[0] == "active"
    assert retryable("active")


def test_the_repair_for_a_suspension_names_the_account_and_forbids_retrying():
    text = repair("suspended", DEAD)
    assert "beta-inc" in text
    assert "Retrying cannot help" in text


def test_the_account_falls_back_rather_than_raising():
    assert account_of({"id": 1}) == "an unnamed account"
    assert account_of(None) == "an unnamed account"
