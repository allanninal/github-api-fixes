from github_app_event_subscriptions import (
    gating_permission, holds, normalize, repair_steps, rows, seen_events,
    subscription_state, verdict,
)

SUBSCRIBED = ["push", "issues"]
PERMISSIONS = {"contents": "read", "issues": "write", "metadata": "read"}


def test_names_are_normalised_for_case_and_space_only():
    assert normalize("  Pull_Request ") == "pull_request"
    assert normalize("pull-request") == "pull-request"


def test_a_misspelled_event_stays_unknown_rather_than_being_corrected():
    assert gating_permission("pull-request") is None
    assert gating_permission("pull_request") == "pull_requests"


def test_metadata_counts_as_held_even_when_absent_from_the_map():
    assert holds({}, "metadata")
    assert not holds({}, "checks")
    assert not holds({"checks": "none"}, "checks")
    assert holds({"checks": "read"}, "checks")


def test_an_unsubscribed_event_without_its_permission_is_blocked():
    state, detail = subscription_state("pull_request_review_thread", SUBSCRIBED,
                                       PERMISSIONS)
    assert state == "not-subscribed-blocked"
    assert "pull_requests permission" in detail
    assert "cannot be ticked" in detail


def test_an_unsubscribed_event_whose_permission_is_held_is_a_lighter_repair():
    state, detail = subscription_state("release", SUBSCRIBED, PERMISSIONS)
    assert state == "not-subscribed-permitted"
    assert "contents permission" in detail


def test_an_unknown_event_gets_a_subscription_answer_and_no_permission_guess():
    state, detail = subscription_state("sponsorship_tier_change", SUBSCRIBED,
                                       PERMISSIONS)
    assert state == "not-subscribed-gate-unknown"
    assert "does not know which permission" in detail


def test_a_subscribed_event_seen_in_the_log_is_healthy():
    seen = seen_events([{"event": "push"}, {"event": "Push"}, {"nope": 1}])
    assert seen == {"push"}
    assert subscription_state("push", SUBSCRIBED, PERMISSIONS, seen)[0] == \
        "subscribed-and-arriving"


def test_silence_in_the_delivery_log_is_never_a_finding_on_its_own():
    state, detail = subscription_state("issues", SUBSCRIBED, PERMISSIONS, set())
    assert state == "subscribed-not-yet-seen"
    assert "rather than that it is broken" in detail


def test_any_unsubscribed_handler_makes_the_whole_report_unreachable():
    report = rows(["push", "release"], SUBSCRIBED, PERMISSIONS, {"push"})
    state, detail = verdict(report)
    assert state == "handlers-unreachable"
    assert "1 of 2" in detail


def test_a_fully_subscribed_quiet_app_is_not_reported_as_broken():
    report = rows(["push", "issues"], SUBSCRIBED, PERMISSIONS, {"push"})
    assert verdict(report)[0] == "all-subscribed-some-quiet"


def test_a_fully_subscribed_busy_app_is_clean():
    report = rows(["push", "issues"], SUBSCRIBED, PERMISSIONS, {"push", "issues"})
    assert verdict(report)[0] == "all-subscribed"


def test_no_handled_events_is_not_a_pass():
    assert verdict([])[0] == "nothing-handled"


def test_the_repair_puts_the_permission_before_the_subscription():
    report = rows(["pull_request_review_thread"], SUBSCRIBED, PERMISSIONS)
    steps = repair_steps(report)
    assert len(steps) == 3
    assert "add the pull_requests permission" in steps[0]
    assert "subscribe the App to pull_request_review_thread" in steps[1]
    assert "accept" in steps[2]


def test_the_permission_step_is_skipped_when_the_permission_is_already_held():
    steps = repair_steps(rows(["release"], SUBSCRIBED, PERMISSIONS))
    assert len(steps) == 2
    assert steps[0].startswith("subscribe the App to release")


def test_a_clean_report_has_no_repair():
    assert repair_steps(rows(["push"], SUBSCRIBED, PERMISSIONS, {"push"})) == []
