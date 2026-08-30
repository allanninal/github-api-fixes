from github_webhook_vs_poll import coverage, poll_cost, subscribed_events, verdict

ACTIVE = [{"id": 1, "active": True, "events": ["issues", "issue_comment"]}]
DISABLED = [{"id": 2, "active": False, "events": ["issues"]}]
WILDCARD = [{"id": 3, "active": True, "events": ["*"]}]


def test_active_and_inactive_subscriptions_are_kept_apart():
    subs = subscribed_events(ACTIVE + DISABLED)
    assert "issue_comment" in subs["events"]
    assert subs["inactive"] == {"issues"}
    assert subs["wildcard"] is False


def test_a_wildcard_is_recognised_only_when_the_hook_is_active():
    assert subscribed_events(WILDCARD)["wildcard"] is True
    off = [{"id": 4, "active": False, "events": ["*"]}]
    assert subscribed_events(off)["wildcard"] is False
    assert subscribed_events(off)["inactive_wildcard"] is True


def test_junk_in_the_hook_list_does_not_raise():
    assert subscribed_events([None, "nope", {}])["events"] == set()
    assert subscribed_events(None)["events"] == set()


def test_an_active_hook_covers_its_concern():
    rows = coverage(["issues", "pulls"], ACTIVE)
    assert rows[0]["state"] == "covered"
    assert rows[1]["state"] == "uncovered"


def test_a_disabled_hook_is_uncovered_and_says_why():
    rows = coverage(["issues"], DISABLED)
    assert rows[0]["state"] == "uncovered"
    assert "not active" in rows[0]["detail"]


def test_a_wildcard_covers_everything_and_warns_that_it_does():
    rows = coverage(["issues", "commits", "releases"], WILDCARD)
    assert [r["state"] for r in rows] == ["covered"] * 3
    assert "everything else" in rows[0]["detail"]


def test_an_unknown_concern_is_matched_against_its_own_name():
    rows = coverage(["deployment"], [{"active": True, "events": ["deployment"]}])
    assert rows[0]["state"] == "covered"


def test_no_hooks_at_all_leaves_every_concern_uncovered():
    rows = coverage(["issues", "pulls"], [])
    assert all(r["state"] == "uncovered" for r in rows)
    assert "no hook subscribes" in rows[0]["detail"]


def test_the_poll_costs_endpoints_times_repos_times_the_clock():
    cost = poll_cost(["issues", "pulls"], 60, repos=3)
    assert cost["requests_per_hour"] == 360
    assert cost["requests_per_day"] == 8640


def test_latency_is_half_the_interval_on_average_and_all_of_it_at_worst():
    cost = poll_cost(["issues"], 60)
    assert cost["mean_latency_s"] == 30
    assert cost["worst_latency_s"] == 60


def test_a_zero_interval_is_clamped_rather_than_dividing_by_zero():
    assert poll_cost(["issues"], 0)["requests_per_hour"] == 3600


def test_an_uncovered_concern_is_reported_with_both_numbers():
    rows = coverage(["issues", "pulls"], [])
    state, detail = verdict(rows, poll_cost(["issues", "pulls"], 60, repos=3))
    assert state == "polling"
    assert "2 of 2" in detail
    assert "360 request(s)" in detail
    assert "30s late" in detail


def test_a_loop_spending_half_the_quota_is_called_out_as_such():
    rows = coverage(["issues", "pulls"], [])
    state, detail = verdict(rows, poll_cost(["issues", "pulls"], 1, repos=1))
    assert state == "polling-dominates"
    assert "%" in detail


def test_full_coverage_reframes_the_loop_as_reconciliation():
    state, detail = verdict(coverage(["issues"], ACTIVE), poll_cost(["issues"], 3600))
    assert state == "push"
    assert "reconciliation" in detail


def test_polling_nothing_is_its_own_state():
    assert verdict([], poll_cost([], 60))[0] == "nothing-polled"
