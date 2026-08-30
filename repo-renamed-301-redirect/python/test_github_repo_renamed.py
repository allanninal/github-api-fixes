from github_repo_renamed import (
    PERMANENT, TEMPORARY, durable_key, extra_round_trips, is_permanent,
    is_redirect, read_cost, repair, repo_from_location, same_repo, verdict,
)

BY_ID = "https://api.github.com/repositories/1300192"
BY_NAME = "https://api.github.com/repos/acme/core-api"


def test_permanent_and_temporary_redirects_are_kept_apart():
    assert is_redirect(301) and is_permanent(301)
    assert is_redirect(308) and is_permanent(308)
    assert is_redirect(302) and not is_permanent(302)
    assert is_redirect(307) and not is_permanent(307)
    assert not is_redirect(200)
    assert not is_redirect(None)
    assert set(PERMANENT).isdisjoint(TEMPORARY)


def test_the_location_usually_names_an_id_rather_than_a_name():
    assert repo_from_location(BY_ID) == ("id", "1300192")
    assert repo_from_location(BY_NAME) == ("full_name", "acme/core-api")
    assert repo_from_location("/repos/acme/core-api") == ("full_name", "acme/core-api")
    assert repo_from_location("https://example.test/nothing") is None
    assert repo_from_location(None) is None


def test_names_are_compared_the_way_github_compares_them():
    assert same_repo("Acme/Platform", "acme/platform")
    assert same_repo(" acme/platform ", "acme/platform")
    assert not same_repo("acme/platform", "acme/core-api")
    assert not same_repo(None, "acme/platform")


def test_a_permanent_redirect_is_the_finding_and_names_the_target():
    state, detail = verdict("acme/platform-api", 301, BY_ID, "acme/core-api")
    assert state == "renamed-permanent"
    assert "1300192" in detail
    assert "acme/core-api" in detail


def test_a_permanent_redirect_without_a_location_is_still_a_finding():
    state, detail = verdict("acme/platform-api", 301, None, None)
    assert state == "renamed-permanent"
    assert "no usable Location" in detail


def test_a_temporary_redirect_must_not_be_written_into_a_config():
    state, detail = verdict("acme/platform-api", 302, BY_NAME, None)
    assert state == "moved-temporary"
    assert "change nothing" in detail
    assert "change nothing" in repair(state)


def test_a_followed_redirect_is_caught_by_the_name_that_came_back():
    state, detail = verdict("acme/platform-api", 200, None, "acme/core-api")
    assert state == "renamed-followed"
    assert "nobody was told" in detail


def test_capitalisation_is_not_a_rename():
    state, detail = verdict("Acme/Platform", 200, None, "acme/platform")
    assert state == "case-only"
    assert "capitalisation" in detail
    assert repair(state).startswith("nothing.")


def test_a_matching_name_is_not_a_finding():
    assert verdict("acme/core-api", 200, None, "acme/core-api")[0] == "current"
    assert repair("current") == "nothing."


def test_a_404_is_handed_to_the_note_that_owns_it():
    state, detail = verdict("acme/gone", 404, None, None)
    assert state == "not-found"
    assert "not a rename" in detail
    assert "triage the 404" in repair(state)


def test_an_unreadable_probe_is_never_reported_as_a_rename():
    assert verdict("acme/x", None, None, None)[0] == "unknown"
    assert verdict("acme/x", 500, None, None)[0] == "unknown"
    assert verdict("acme/x", 200, None, None)[0] == "unknown"


def test_the_durable_key_is_what_the_repair_is_really_about():
    assert durable_key({"id": 1300192, "node_id": "R_kgDOE", "name": "core-api"}) == {
        "id": 1300192, "node_id": "R_kgDOE"}
    assert durable_key({"name": "core-api"}) is None
    assert durable_key(None) is None


def test_a_followed_redirect_doubles_the_requests_on_that_path():
    assert extra_round_trips(1200) == 1200
    assert extra_round_trips(0) == 0
    assert extra_round_trips(-5) == 0
    assert extra_round_trips(None) == 0


def test_the_two_rename_repairs_both_point_at_the_id():
    assert "node_id" in repair("renamed-permanent")
    assert "node_id" in repair("renamed-followed")
    assert "following a redirect silently" in repair("renamed-followed")


def test_the_cost_is_stated_as_the_upper_bound_it_is():
    assert read_cost(["a/b", "c/d"]) == 4
    assert read_cost(["a/b"]) == 2
    assert read_cost([]) == 0
    assert read_cost(None) == 0
