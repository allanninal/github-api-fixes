from github_code_search_budget import (
    buckets, collapsed_cost, scan_cost, seconds_until, verdict)

PAYLOAD = {"resources": {
    "core": {"limit": 5000, "remaining": 4987, "reset": 1700000000},
    "search": {"limit": 30, "remaining": 30, "reset": 1700000060},
    "code_search": {"limit": 10, "remaining": 0, "reset": 1700000060},
}}


def test_every_documented_bucket_is_reported_separately():
    table = buckets(PAYLOAD)
    assert table["core"]["remaining"] == 4987
    assert table["code_search"]["remaining"] == 0
    assert table["code_search"]["limit"] == 10


def test_a_missing_row_is_flagged_rather_than_read_as_zero():
    table = buckets({"resources": {"core": {"limit": 5000, "remaining": 10}}})
    assert table["code_search"]["present"] is False
    assert table["code_search"]["remaining"] is None
    assert table["code_search"]["limit"] == 10


def test_an_empty_payload_still_returns_the_full_table():
    table = buckets(None)
    assert set(table) == {"core", "search", "code_search"}
    assert all(row["present"] is False for row in table.values())


def test_unreadable_numbers_do_not_become_zero():
    table = buckets({"resources": {"code_search": {"limit": "ten", "remaining": None}}})
    assert table["code_search"]["limit"] == 10
    assert table["code_search"]["remaining"] is None


def test_a_per_repo_scan_costs_repositories_not_pages():
    cost = scan_cost(600, 1, 10)
    assert cost["requests"] == 600
    assert cost["minutes"] == 60


def test_minutes_round_up_because_a_partial_minute_still_waits():
    assert scan_cost(11, 1, 10)["minutes"] == 2
    assert scan_cost(0, 3, 10) == {"requests": 0, "minutes": 0}


def test_the_collapsed_scan_costs_pages():
    cost = collapsed_cost(1, 800, 10)
    assert cost["pages_per_query"] == 8
    assert cost["requests"] == 8
    assert cost["minutes"] == 1
    assert cost["truncated"] is False


def test_paging_stops_at_the_thousand_result_ceiling():
    cost = collapsed_cost(1, 50000, 10)
    assert cost["pages_per_query"] == 10
    assert cost["truncated"] is True


def test_a_query_with_no_matches_still_costs_one_request():
    assert collapsed_cost(3, 0, 10)["requests"] == 3


def test_the_page_size_cannot_be_raised_past_a_hundred():
    assert collapsed_cost(1, 500, 10, page_size=500)["pages_per_query"] == 5


def test_seconds_until_floors_at_zero_and_reports_junk_as_unknown():
    assert seconds_until(1700000060, 1700000000) == 60
    assert seconds_until(1700000000, 1700000060) == 0
    assert seconds_until(None, 1700000000) is None


def test_an_empty_code_search_bucket_is_not_the_hourly_quota():
    state, detail = verdict(buckets(PAYLOAD)["code_search"],
                            scan_cost(600, 1, 10), collapsed_cost(1, 800, 10))
    assert state == "exhausted"
    assert "not the core quota" in detail


def test_the_loop_is_named_as_the_cost_when_it_dwarfs_the_query():
    bucket = {"limit": 10, "remaining": 10, "reset": 0, "present": True}
    state, detail = verdict(bucket, scan_cost(600, 1, 10), collapsed_cost(1, 800, 10))
    assert state == "per-repo-scan"
    assert "600 request(s)" in detail
    assert "8 request(s)" in detail


def test_a_scan_inside_one_minute_is_clear():
    bucket = {"limit": 10, "remaining": 10, "reset": 0, "present": True}
    state, _ = verdict(bucket, scan_cost(5, 1, 10), collapsed_cost(1, 200, 10))
    assert state == "clear"


def test_a_missing_row_is_said_out_loud_in_the_verdict():
    bucket = {"limit": 10, "remaining": None, "reset": None, "present": False}
    _, detail = verdict(bucket, scan_cost(5, 1, 10), collapsed_cost(1, 200, 10))
    assert "documented default" in detail


def test_nothing_to_cost_is_its_own_state():
    bucket = {"limit": 10, "remaining": 10, "reset": 0, "present": True}
    assert verdict(bucket, scan_cost(0, 0, 10), collapsed_cost(1, 200, 10))[0] == "no-scan"
