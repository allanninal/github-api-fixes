from datetime import datetime, timezone

from github_hook_active_audit import (
    active_state, classify, days_since, edited_after_creation, failed_last,
    last_code, newest_delivery, repair, silent_days, summarize,
)

NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)

FRESH = {"id": 1, "active": True, "created_at": "2026-01-04T10:00:00Z",
         "updated_at": "2026-01-04T10:00:01Z"}
TOGGLED = {"id": 2, "active": False, "created_at": "2026-01-04T10:00:00Z",
           "updated_at": "2026-07-26T02:11:00Z"}
BORN_OFF = {"id": 3, "active": False, "created_at": "2026-01-04T10:00:00Z",
            "updated_at": "2026-01-04T10:00:02Z"}
DISABLED = {"id": 4, "active": False, "created_at": "2026-01-04T10:00:00Z",
            "updated_at": "2026-07-26T02:11:00Z",
            "last_response": {"code": 502, "status": "bad gateway"}}


def test_a_truthy_test_would_get_the_string_false_wrong():
    assert active_state({"active": "false"}) == "off"
    assert active_state({"active": "0"}) == "off"
    assert active_state({"active": 0}) == "off"
    assert active_state({"active": "true"}) == "on"
    assert active_state({"active": 1}) == "on"


def test_an_absent_flag_is_unknown_and_not_off():
    assert active_state({"id": 1}) == "unknown"
    assert active_state({"active": None}) == "unknown"
    assert active_state({"active": "maybe"}) == "unknown"
    assert active_state(None) == "unknown"


def test_the_last_response_code_survives_every_shape_it_arrives_in():
    assert last_code(DISABLED) == 502
    assert last_code({"last_response": {"code": "500"}}) == 500
    assert last_code({"last_response": {"code": None}}) is None
    assert last_code({"last_response": {}}) is None
    assert last_code({"id": 1}) is None
    assert failed_last(DISABLED)
    assert not failed_last({"last_response": {"code": 200}})


def test_a_hook_configured_in_one_call_is_not_called_edited():
    assert edited_after_creation(BORN_OFF) is False
    assert edited_after_creation(TOGGLED) is True
    assert edited_after_creation({"created_at": "2026-01-04T10:00:00Z"}) is None


def test_the_three_routes_to_off_are_three_different_states():
    assert classify(DISABLED, None, NOW)[0] == "inactive-after-failures"
    assert classify(TOGGLED, None, NOW)[0] == "inactive-toggled"
    assert classify(BORN_OFF, None, NOW)[0] == "inactive-since-creation"


def test_a_disabled_hook_is_never_reported_as_a_plain_toggle():
    state, detail = classify(DISABLED, None, NOW)
    assert state == "inactive-after-failures"
    assert "502" in detail
    assert "aftermath" in detail


def test_an_off_hook_with_no_timestamps_says_so_rather_than_guessing():
    state, detail = classify({"id": 9, "active": False}, None, NOW)
    assert state == "inactive-undated"
    assert "cannot be told from here" in detail


def test_an_on_hook_with_an_empty_log_is_sent_to_a_different_question():
    state, detail = classify(FRESH, [], NOW)
    assert state == "active-but-silent"
    assert "not the problem" in detail
    assert "events array" in repair(state, FRESH)


def test_an_on_hook_with_a_recent_delivery_is_simply_active():
    log = [{"delivered_at": "2026-08-30T09:00:00Z", "status": "OK"}]
    assert classify(FRESH, log, NOW)[0] == "active"


def test_the_delivery_log_is_read_for_its_newest_row_not_its_first():
    log = [{"delivered_at": "2026-08-01T09:00:00Z"},
           {"delivered_at": "2026-08-29T09:00:00Z"},
           {"delivered_at": "not a date"},
           "junk"]
    assert newest_delivery(log) == "2026-08-29T09:00:00Z"
    assert silent_days(log, NOW) == 1
    assert newest_delivery([]) is None
    assert silent_days([], NOW) is None


def test_the_repair_for_a_disabled_hook_puts_the_receiver_first():
    text = repair("inactive-after-failures", DISABLED, "acme-corp/api")
    assert text.index("fix the receiver") < text.index("re-enable")
    assert "/repos/acme-corp/api/hooks/4" in text


def test_the_summary_counts_the_hooks_that_are_off():
    stats = summarize([FRESH, TOGGLED, DISABLED, {"id": 5}])
    assert stats["total"] == 4
    assert stats["inactive"] == 2
    assert stats["active"] == 1
    assert stats["inactive_ids"] == [2, 4]


def test_days_since_refuses_to_invent_an_age():
    assert days_since("2026-08-27T12:00:00Z", NOW) == 3
    assert days_since("", NOW) is None
    assert days_since("null", NOW) is None
