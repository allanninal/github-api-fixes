from github_hook_event_coverage import coverage, normalize


def rows_by_event(rows):
    return {r["event"]: r for r in rows}


def test_normalize_accepts_the_three_spellings_people_use():
    assert normalize("pull_request") == "pull_request"
    assert normalize("pull-request") == "pull_request"
    assert normalize("Pull_Request.opened") == "pull_request"
    assert normalize(None) == ""


def test_an_unsubscribed_handler_is_the_finding():
    rows = rows_by_event(coverage(["release"], ["push", "pull_request"], ["push"]))
    assert rows["release"]["state"] == "missing"


def test_an_action_suffix_matches_the_event_it_belongs_to():
    # pull_request.opened is not something a hook can subscribe to, and treating
    # it as a separate event invents a repair that cannot be carried out.
    rows = rows_by_event(coverage(["pull_request.opened"], ["pull_request"],
                                  ["pull_request"]))
    assert rows["pull_request"]["state"] == "delivered"
    assert "GitHub spells this" in rows["pull_request"]["note"]


def test_subscribed_but_unseen_is_not_the_same_as_unsubscribed():
    rows = rows_by_event(coverage(["release"], ["release", "push"], ["push"]))
    assert rows["release"]["state"] == "quiet"


def test_a_wildcard_is_reported_rather_than_counted_as_success():
    rows = rows_by_event(coverage(["release"], ["*"], ["push"]))
    assert rows["release"]["state"] == "wildcard"


def test_traffic_nothing_handles_is_reported_too():
    rows = rows_by_event(coverage(["push"], ["push", "status"],
                                  ["push", "status", "status"]))
    assert rows["status"]["state"] == "unhandled"
    assert rows["status"]["seen"] == 2
    assert rows["push"]["state"] == "delivered"


def test_an_event_arriving_without_a_subscription_is_still_surfaced():
    rows = rows_by_event(coverage(["push"], ["push"], ["push", "ping"]))
    assert rows["ping"]["state"] == "unhandled"
    assert "without a subscription" in rows["ping"]["note"]


def test_case_and_hyphens_do_not_create_phantom_findings():
    rows = coverage(["Pull-Request"], ["pull_request"], ["pull_request"])
    assert [r["state"] for r in rows] == ["delivered"]
