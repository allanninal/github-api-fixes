from github_graphql_nested import (
    POINTS_PER_QUERY, auditable, classify, connection_fields, followup_queries,
    is_connection, missing, operations, outer_text, point_cost, refusal, repair,
    resumable, truncated, unauditable, unresumable, walk_connections,
)

DATA = {"repositoryOwner": {"repositories": {
    "totalCount": 218,
    "pageInfo": {"hasNextPage": True, "endCursor": "Y3Vyc29yOjU="},
    "nodes": [
        {"name": "monorepo",
         "issues": {"totalCount": 406, "nodes": [{"number": 1}, {"number": 2}]}},
        {"name": "tiny",
         "issues": {"totalCount": 2, "nodes": [{"number": 9}, {"number": 10}]}},
    ],
}}}

NESTED_QUERY = ("query { repositoryOwner(login: \"acme\") {"
                " repositories(first: 5) { totalCount"
                " pageInfo { hasNextPage endCursor }"
                " nodes { name issues(first: 5) { totalCount nodes { number } } } } } }")


def test_the_walk_finds_inner_connections_by_path():
    entries = walk_connections(DATA)
    paths = [e["path"] for e in entries]
    assert "repositoryOwner.repositories" in paths
    assert "repositoryOwner.repositories.nodes[0].issues" in paths
    assert "repositoryOwner.repositories.nodes[1].issues" in paths


def test_an_inner_connection_is_deeper_than_the_one_containing_it():
    by_path = {e["path"]: e for e in walk_connections(DATA)}
    assert by_path["repositoryOwner.repositories"]["depth"] == 0
    assert by_path["repositoryOwner.repositories.nodes[0].issues"]["depth"] == 1


def test_truncation_is_measured_per_parent_and_not_in_total():
    by_path = {e["path"]: e for e in walk_connections(DATA)}
    big = by_path["repositoryOwner.repositories.nodes[0].issues"]
    small = by_path["repositoryOwner.repositories.nodes[1].issues"]
    assert missing(big) == 404
    assert truncated(big)
    assert missing(small) == 0
    assert not truncated(small)


def test_has_next_page_alone_is_enough_to_call_it_truncated():
    entry = {"depth": 1, "returned": 100, "total_count": None,
             "has_next_page": True, "end_cursor": "abc"}
    assert truncated(entry)
    assert missing(entry) is None
    assert auditable(entry)


def test_a_connection_with_neither_field_cannot_be_judged_at_all():
    entry = {"depth": 1, "returned": 100, "total_count": None,
             "has_next_page": None, "end_cursor": None}
    assert not auditable(entry)
    assert not truncated(entry)
    state, detail = classify([{"depth": 0, "returned": 5, "total_count": 5,
                               "has_next_page": False, "end_cursor": None}, entry])
    assert state == "inner-connection-unauditable"
    assert "neither totalCount nor pageInfo" in detail


def test_seeing_the_gap_and_being_able_to_resume_it_are_different():
    seen_only = {"depth": 1, "returned": 5, "total_count": 406,
                 "has_next_page": None, "end_cursor": None}
    assert truncated(seen_only)
    assert not resumable(seen_only)
    assert resumable({"depth": 1, "returned": 5, "total_count": 406,
                      "has_next_page": True, "end_cursor": "abc"})


def test_the_inner_truncation_outranks_the_outer_one():
    state, detail = classify(walk_connections(DATA))
    assert state == "inner-connection-truncated"
    assert "404" in detail
    assert "after: endCursor" in repair(state)


def test_an_outer_only_truncation_is_named_as_the_one_people_notice():
    data = {"repositories": {"totalCount": 218,
                             "pageInfo": {"hasNextPage": True, "endCursor": "c"},
                             "nodes": [{"name": "tiny",
                                        "issues": {"totalCount": 2,
                                                   "nodes": [{"number": 1}, {"number": 2}]}}]}}
    state, detail = classify(walk_connections(data))
    assert state == "outer-connection-truncated"
    assert "do notice" in detail


def test_a_complete_response_is_not_reported_as_a_finding():
    data = {"repositories": {"totalCount": 1,
                             "pageInfo": {"hasNextPage": False, "endCursor": None},
                             "nodes": [{"name": "tiny",
                                        "issues": {"totalCount": 1,
                                                   "nodes": [{"number": 1}]}}]}}
    assert classify(walk_connections(data))[0] == "complete"
    assert classify([])[0] == "no-connection-in-the-response"


def test_an_inner_page_info_is_never_credited_to_its_parent():
    doc = ("query { a(first: 10) { totalCount nodes {"
           " b(first: 10) { pageInfo { hasNextPage } nodes { id } } } } }")
    fields = {f["field"]: f for f in connection_fields(doc)}
    assert fields["a"]["has_total_count"] and not fields["a"]["has_page_info"]
    assert fields["b"]["has_page_info"] and not fields["b"]["has_total_count"]
    assert fields["a"]["depth"] == 0 and fields["b"]["depth"] == 1
    assert "pageInfo" not in outer_text(" totalCount nodes { pageInfo { x } } ")


def test_the_document_audit_names_what_cannot_be_checked_or_resumed():
    fields = connection_fields(NESTED_QUERY)
    assert [f["field"] for f in unresumable(fields)] == ["issues"]
    assert unauditable(fields) == []
    bare = "query { a(first: 5) { nodes { b(first: 5) { nodes { id } } } } }"
    assert [f["field"] for f in unauditable(connection_fields(bare))] == ["b"]


def test_a_connection_is_recognised_by_nodes_or_edges():
    assert is_connection({"nodes": []})
    assert is_connection({"edges": []})
    assert not is_connection({"nodes": 3})
    assert not is_connection({"name": "monorepo"})


def test_the_cost_of_doing_it_properly_is_counted_before_the_loop_is_written():
    assert followup_queries(walk_connections(DATA)) == 202
    assert followup_queries([]) == 0
    assert POINTS_PER_QUERY == 1
    assert point_cost(1) == 1
    assert point_cost(0) == 0


def test_the_script_refuses_to_send_a_mutation():
    assert operations("query Q { viewer { login } }") == ["query"]
    assert refusal("mutation M { addStar(input: {}) { clientMutationId } }")
    assert refusal("subscription S { thing { id } }")
    assert refusal(NESTED_QUERY) is None
