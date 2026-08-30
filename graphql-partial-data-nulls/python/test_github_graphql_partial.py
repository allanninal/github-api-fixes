from github_graphql_partial import (
    MISSING, absent, classify, error_paths, is_partial_success, null_paths, operations,
    orphan_error_paths, path_key, permission_hint, point_cost, refusal, repair,
    safe_to_aggregate, tally, unpathed_errors, value_at, withheld,
)

PARTIAL = {
    "data": {"repository": {
        "name": "monorepo",
        "isPrivate": True,
        "diskUsage": None,
        "licenseInfo": None,
        "collaborators": None,
    }},
    "errors": [
        {"type": "FORBIDDEN", "path": ["repository", "diskUsage"],
         "message": "Resource not accessible by personal access token"},
        {"type": "FORBIDDEN", "path": ["repository", "collaborators"],
         "message": "Must have push access to view repository collaborators."},
    ],
}

IN_A_LIST = {
    "data": {"repository": {"pullRequests": {"nodes": [
        {"number": 1, "author": {"login": "ada"}},
        {"number": 2, "author": None},
    ]}}},
    "errors": [{"type": "FORBIDDEN",
                "path": ["repository", "pullRequests", "nodes", 1, "author"]}],
}

TOTAL_FAILURE = {"data": {"repository": None},
                 "errors": [{"type": "NOT_FOUND", "path": ["repository"]}]}

CLEAN = {"data": {"repository": {"name": "monorepo", "isPrivate": False}}}


def test_a_partial_response_is_a_third_outcome_not_a_failure():
    assert is_partial_success(PARTIAL)
    assert not is_partial_success(TOTAL_FAILURE)
    assert not is_partial_success(CLEAN)


def test_withheld_and_absent_are_the_two_kinds_of_null():
    assert withheld(PARTIAL) == ["repository.collaborators", "repository.diskUsage"]
    assert absent(PARTIAL) == ["repository.licenseInfo"]


def test_a_null_with_no_errors_entry_is_a_real_answer():
    body = {"data": {"repository": {"name": "x", "licenseInfo": None}}}
    assert withheld(body) == []
    assert absent(body) == ["repository.licenseInfo"]
    state, detail = classify(body)
    assert state == "nulls-unexplained"
    assert "genuinely empty" in detail


def test_error_paths_survive_a_list_index():
    assert path_key(["repository", "pullRequests", "nodes", 1, "author"]) == \
        "repository.pullRequests.nodes.1.author"
    assert withheld(IN_A_LIST) == ["repository.pullRequests.nodes.1.author"]
    assert absent(IN_A_LIST) == []


def test_the_path_resolver_walks_lists_as_well_as_objects():
    data = IN_A_LIST["data"]
    assert value_at(data, "repository.pullRequests.nodes.0.number") == 1
    assert value_at(data, "repository.pullRequests.nodes.1.author") is None
    assert value_at(data, "repository.pullRequests.nodes.9") is MISSING
    assert value_at(data, "repository.nothingLikeThis") is MISSING
    assert null_paths({"a": None, "b": {"c": None, "d": 1}}) == ["a", "b.c"]


def test_an_error_path_that_matches_no_null_is_reported_not_swallowed():
    body = {"data": {"repository": {"name": "x"}},
            "errors": [{"type": "FORBIDDEN", "path": ["repository", "gone"]}]}
    assert orphan_error_paths(body) == ["repository.gone"]
    assert withheld(body) == []


def test_an_error_with_no_path_cannot_be_attributed():
    body = {"data": {"repository": {"name": "x"}},
            "errors": [{"type": "INTERNAL", "message": "something broke"}]}
    assert unpathed_errors(body) == 1
    assert error_paths(body) == {}
    state, _ = classify(body)
    assert state == "errors-without-path"
    assert "verbatim" in repair(state)


def test_a_query_where_nothing_resolved_belongs_to_the_other_note():
    state, detail = classify(TOTAL_FAILURE)
    assert state == "total-failure"
    assert "failed query wearing a 200" in detail
    assert "graphql-200-with-errors" in repair(state)


def test_the_finding_names_the_paths_rather_than_counting_errors():
    state, detail = classify(PARTIAL)
    assert state == "partial-withheld"
    assert "errors[].path" in detail
    assert "unknown, not zero" in repair(state)
    assert "Do not retry" in repair(state)


def test_an_aggregate_over_a_root_with_withheld_fields_is_a_lower_bound():
    ok, sentence = safe_to_aggregate(PARTIAL, "repository")
    assert not ok
    assert "lower bound" in sentence
    ok2, sentence2 = safe_to_aggregate(PARTIAL, "viewer")
    assert ok2
    assert "is a total" in sentence2


def test_the_aggregation_root_is_matched_on_a_boundary_not_a_prefix():
    body = {"data": {"repo": {"a": None}, "repository": {"b": 1}},
            "errors": [{"type": "FORBIDDEN", "path": ["repo", "a"]}]}
    ok, _ = safe_to_aggregate(body, "repository")
    assert ok


def test_each_withheld_field_names_the_permission_it_would_want():
    assert "admin" in permission_hint("repository.diskUsage")
    assert "members" in permission_hint("repository.collaborators")
    assert permission_hint("repository.somethingNew") == \
        "the permission that covers this field"


def test_the_tally_counts_all_four_kinds_of_thing():
    assert tally(PARTIAL) == {"withheld": 2, "absent": 1, "orphaned": 0,
                              "unpathed_errors": 0}
    assert tally(CLEAN) == {"withheld": 0, "absent": 0, "orphaned": 0,
                            "unpathed_errors": 0}


def test_a_clean_response_says_so_plainly():
    state, _ = classify(CLEAN)
    assert state == "complete"
    assert repair(state) == "nothing."
    assert classify(None)[0] == "unreadable"


def test_the_script_refuses_to_send_a_mutation():
    assert operations("query Q { viewer { login } }") == ["query"]
    assert refusal("mutation M { addStar(input: {}) { clientMutationId } }")
    assert refusal("subscription S { thing { id } }")
    assert refusal("query Q { repository(owner: \"o\", name: \"n\") { name } }") is None


def test_the_run_says_what_it_will_spend():
    assert point_cost(1) == 1
    assert point_cost(0) == 0
    assert point_cost(None) == 0
