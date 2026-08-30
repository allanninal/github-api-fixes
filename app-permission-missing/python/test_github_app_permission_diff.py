from github_app_permission_diff import diff, parse_accepted


def test_the_header_parses_to_name_and_level_pairs():
    assert parse_accepted("pull_requests=write") == [("pull_requests", "write")]
    assert parse_accepted("contents=read, metadata=read") == [
        ("contents", "read"), ("metadata", "read")]
    assert parse_accepted("issues=write; pull_requests=write") == [
        ("issues", "write"), ("pull_requests", "write")]


def test_an_absent_header_parses_to_nothing_rather_than_a_guess():
    assert parse_accepted(None) == []
    assert parse_accepted("") == []
    assert parse_accepted("garbage-with-no-equals") == []


def test_a_403_with_no_header_is_not_a_permission_problem():
    # GET /user under an installation token. No permission will ever open it.
    state, detail = diff({"contents": "read"}, [], 403)
    assert state == "endpoint-refuses-apps"
    assert "installation token" in detail


def test_read_where_write_is_needed_is_its_own_state():
    state, detail = diff({"pull_requests": "read"},
                         parse_accepted("pull_requests=write"))
    assert state == "level-too-low"
    assert "has read and needs write" in detail


def test_a_permission_that_is_absent_is_named():
    state, detail = diff({"contents": "read"},
                         parse_accepted("pull_requests=write"))
    assert state == "permission-absent"
    assert "pull_requests: write" in detail


def test_holding_everything_asked_for_points_elsewhere():
    state, detail = diff({"pull_requests": "write", "metadata": "read"},
                         parse_accepted("pull_requests=write, metadata=read"))
    assert state == "sufficient"
    assert "accepted" in detail


def test_write_satisfies_a_read_requirement():
    assert diff({"contents": "write"}, parse_accepted("contents=read"))[0] == "sufficient"


def test_an_unreadable_map_is_not_an_empty_one():
    # None means "wrong credential"; {} means "the App holds nothing".
    assert diff(None, parse_accepted("issues=write"))[0] == "needed"
    assert diff({}, parse_accepted("issues=write"))[0] == "permission-absent"


def test_a_success_and_a_non_403_are_not_diffed_at_all():
    assert diff({}, parse_accepted("issues=write"), 200)[0] == "accessible"
    state, detail = diff({}, parse_accepted("issues=write"), 404)
    assert state == "not-a-permission-error"
    assert "masked" in detail
