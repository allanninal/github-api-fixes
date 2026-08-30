from github_graphql_search_ceiling import (
    MAX_PAGE_SIZE, SEARCH_RESULT_CEILING, classify_walk, match_count,
    operations, pages_to_ceiling, point_cost, reachable, refusal, repair,
    slices_needed, truncation_signal, typed_connection_for, unreachable,
)


def test_the_ceiling_is_a_property_of_the_index_not_the_page_size():
    assert SEARCH_RESULT_CEILING == 1000
    assert MAX_PAGE_SIZE == 100
    assert pages_to_ceiling(100) == 10
    assert pages_to_ceiling(30) == 34
    assert pages_to_ceiling(1) == 1000


def test_a_match_count_splits_into_a_reachable_and_an_unreachable_half():
    assert reachable(18231) == 1000
    assert unreachable(18231) == 17231
    assert reachable(400) == 400
    assert unreachable(400) == 0
    assert reachable(None) == 0
    assert unreachable("not a number") == 0


def test_the_ceiling_stop_and_a_complete_walk_are_the_same_shape():
    hit, detail = classify_walk(18231, 1000, False, 10, 11)
    done, _ = classify_walk(40, 40, False, 1, 11)
    assert hit == "ceiling-hit-silently"
    assert done == "complete"
    # Both ended with hasNextPage false and no error. That is the note.
    assert "No error was raised" in detail
    assert "18231" in detail


def test_a_walk_cut_short_by_the_operator_proves_nothing():
    state, detail = classify_walk(18231, 500, True, 5, 5)
    assert state == "stopped-early-by-request"
    assert "nothing about the ceiling is proved" in detail
    assert "at least 10" in repair(state, 18231, "ISSUE")


def test_a_walk_still_going_is_not_a_finding():
    state, _ = classify_walk(18231, 300, True, 3, 11)
    assert state == "still-paging"


def test_ending_below_the_ceiling_is_a_different_note():
    state, detail = classify_walk(900, 640, False, 7, 11)
    assert state == "truncated-early"
    assert "not this note" in detail
    assert "search-incomplete-results" in repair(state, 900, "ISSUE")


def test_the_repair_names_a_ceiling_free_connection_and_a_slice_count():
    fix = repair("ceiling-hit-silently", 18231, "ISSUE")
    assert "repository.issues" in fix
    assert "19 slice(s)" in fix
    assert "organization.repositories" in repair(
        "ceiling-hit-silently", 4000, "REPOSITORY")


def test_a_partition_needs_one_slice_per_thousand_matches():
    assert slices_needed(18231) == 19
    assert slices_needed(1000) == 1
    assert slices_needed(1001) == 2
    assert slices_needed(0) == 0


def test_every_search_type_has_a_connection_that_has_no_ceiling():
    assert "repository.issues" in typed_connection_for("ISSUE")
    assert "organization.repositories" in typed_connection_for("repository")
    assert "membersWithRole" in typed_connection_for("USER")
    assert "discussions" in typed_connection_for("DISCUSSION")
    assert typed_connection_for("SOMETHING_ELSE").startswith("the typed connection")


def test_the_two_apis_announce_the_same_ceiling_differently():
    assert "422" in truncation_signal("rest")
    assert "1000 search results" in truncation_signal("rest")
    assert "no error at all" in truncation_signal("graphql")
    assert "hasNextPage" in truncation_signal("graphql")


def test_the_count_field_follows_the_search_type():
    search = {"issueCount": 18231, "repositoryCount": 12, "userCount": 3}
    assert match_count(search, "ISSUE") == 18231
    assert match_count(search, "REPOSITORY") == 12
    assert match_count(search, "USER") == 3
    assert match_count(None, "ISSUE") == 0


def test_the_run_says_the_most_it_can_spend():
    assert point_cost(11) == 11
    assert point_cost(0) == 0
    assert point_cost(None) == 0


def test_the_document_this_script_sends_is_a_read():
    assert operations("query($q: String!) { search(query: $q, type: ISSUE, first: 100) { issueCount } }") == ["query"]
    assert refusal("mutation M { addStar(input: {}) { clientMutationId } }")
    assert refusal("subscription S { thing { id } }")
    assert refusal("") == "the document contains no operation to send."
