from github_archived_repo_guard import (
    CORE_QUOTA_PER_HOUR, ORG_PAGE_SIZE, accepts_writes, classify_failure,
    days_since, explain, lifecycle, pages_for, parse_link, quota_share,
    read_cost_for_repos, repair, retry_policy, row_for, skip_list, summarise,
    wasted_requests,
)

ARCHIVED = {"full_name": "acme/legacy-billing", "archived": True,
            "disabled": False, "pushed_at": "2025-01-27T09:14:00Z"}
ACTIVE = {"full_name": "acme/platform-api", "archived": False,
          "disabled": False, "pushed_at": "2026-08-20T09:14:00Z"}
DISABLED = {"full_name": "acme/suspended-thing", "archived": False,
            "disabled": True}
BOTH = {"full_name": "acme/frozen-and-gone", "archived": True, "disabled": True}


def test_the_two_booleans_make_four_states():
    assert lifecycle(ARCHIVED) == "archived"
    assert lifecycle(ACTIVE) == "active"
    assert lifecycle(DISABLED) == "disabled"
    assert lifecycle(BOTH) == "archived-and-disabled"
    assert lifecycle(None) == "unknown"
    assert lifecycle("not a repo") == "unknown"


def test_an_archived_repository_can_never_accept_a_write():
    assert accepts_writes("archived") is False
    assert accepts_writes("archived-and-disabled") is False
    assert accepts_writes("disabled") is False
    assert accepts_writes("active") is True
    assert accepts_writes("unknown") is None


def test_the_output_a_client_needs_is_a_policy_not_a_status_code():
    assert retry_policy("archived") == "permanent-skip"
    assert retry_policy("disabled") == "permanent-skip"
    assert retry_policy("active") == "retry"
    assert retry_policy("unknown") == "unknown"


def test_the_explanation_says_the_token_is_irrelevant():
    assert "regardless of the token" in explain("archived")
    assert "different owner" in explain("disabled")
    assert "would still leave it disabled" in explain("archived-and-disabled")
    assert "unknown" in explain("nonsense")


def test_a_recorded_refusal_is_attributed_without_being_reproduced():
    state, detail = classify_failure(403, "Repository was archived so is read-only.")
    assert state == "archived-refusal"
    assert "not of your credential" in detail
    assert "No token, scope or App permission" in repair(state)


def test_a_rate_limit_is_the_one_403_worth_retrying():
    state, _ = classify_failure(403, "API rate limit exceeded for user ID 1")
    assert state == "rate-limited"
    assert "retry-after" in repair(state)


def test_a_credential_refusal_is_handed_back_to_the_credential():
    state, detail = classify_failure(403, "Resource not accessible by integration")
    assert state == "credential-refusal"
    assert "blames the credential" in detail
    assert classify_failure(404, "Not Found")[0] == "not-found"
    assert classify_failure(403, "Forbidden")[0] == "forbidden-unattributed"
    assert classify_failure("", "")[0] == "no-failure"


def test_the_retry_waste_is_stated_in_requests_and_in_quota():
    assert wasted_requests(12, 3) == 36
    assert wasted_requests(12, 3, 24) == 864
    assert wasted_requests(0, 3) == 0
    assert wasted_requests(None, None) == 0
    assert quota_share(864) == 17
    assert quota_share(0) == 0
    assert CORE_QUOTA_PER_HOUR == 5000


def test_the_skip_list_holds_everything_that_cannot_be_written_to():
    rows = [row_for(ARCHIVED), row_for(ACTIVE), row_for(DISABLED), row_for(BOTH)]
    assert skip_list(rows) == ["acme/frozen-and-gone", "acme/legacy-billing",
                               "acme/suspended-thing"]
    assert skip_list([]) == []
    assert skip_list(None) == []


def test_the_summary_counts_a_repository_in_both_columns_when_it_is_both():
    counts = summarise([row_for(ARCHIVED), row_for(ACTIVE), row_for(BOTH)])
    assert counts == {"total": 3, "archived": 2, "disabled": 1, "writable": 1,
                      "unknown": 0}


def test_a_row_carries_the_policy_and_the_repair_together():
    row = row_for(ARCHIVED)
    assert row["state"] == "archived"
    assert row["retry_policy"] == "permanent-skip"
    assert row["accepts_writes"] is False
    assert "top of the write loop" in row["repair"]
    assert row["days_since_last_push"] is not None


def test_an_age_is_read_from_the_timestamp_or_left_alone():
    from datetime import datetime, timezone
    now = datetime(2026, 8, 31, tzinfo=timezone.utc)
    assert days_since("2026-08-01T00:00:00Z", now) == 30
    assert days_since("2027-01-01T00:00:00Z", now) == 0
    assert days_since(None) is None
    assert days_since("not a date") is None


def test_the_cost_is_worked_out_before_anything_is_fetched():
    assert read_cost_for_repos(["a", "b", "c"]) == 3
    assert read_cost_for_repos([]) == 0
    assert ORG_PAGE_SIZE == 100
    assert pages_for(212) == 3
    assert pages_for(100) == 1
    assert pages_for(0) == 0


def test_the_link_header_survives_a_comma_inside_a_url():
    header = ('<https://api.github.com/orgs/acme/repos?type=all,public&page=2>; '
              'rel="next", <https://api.github.com/orgs/acme/repos?page=3>; rel="last"')
    links = parse_link(header)
    assert links["next"].endswith("page=2")
    assert links["last"].endswith("page=3")
    assert parse_link("") == {}
    assert parse_link(None) == {}
