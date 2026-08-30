from github_graphql_id_crosswalk import (
    classify_pair, classify_store, crosswalk, decode_legacy_node_id, id_space,
    join_rows, join_rows_normalised, migration_split,
    number_is_not_the_database_id, operations, refusal, repair, to_database_id,
)

REST_ISSUE = {"id": 1347, "node_id": "MDU6SXNzdWUxMzQ3", "number": 1347,
              "title": "Found a bug"}
GQL_ISSUE = {"id": "MDU6SXNzdWUxMzQ3", "databaseId": 1347, "number": 1347}
NEW_STYLE = "I_kwDOAbCdEf4AbCdE"


def test_a_legacy_node_id_carries_the_database_id_inside_it():
    assert decode_legacy_node_id("MDU6SXNzdWUxMzQ3") == ("Issue", 1347)
    assert decode_legacy_node_id("MDU6SXNzdWUx") == ("Issue", 1)
    assert decode_legacy_node_id("MDEwOlJlcG9zaXRvcnkxMjk2MjY5") == ("Repository", 1296269)


def test_the_new_format_carries_nothing_and_must_be_refetched():
    assert decode_legacy_node_id(NEW_STYLE) is None
    assert id_space(NEW_STYLE) == "graphql-node-id"
    assert to_database_id(NEW_STYLE) is None


def test_an_ordinary_string_is_not_mistaken_for_an_identifier():
    assert decode_legacy_node_id("aGVsbG8gd29ybGQ=") is None
    assert decode_legacy_node_id("not base64 at all") is None
    assert decode_legacy_node_id("") is None
    assert decode_legacy_node_id("1347") is None


def test_each_identifier_is_placed_in_exactly_one_key_space():
    assert id_space(1347) == "rest-database-id"
    assert id_space("1347") == "rest-database-id"
    assert id_space("MDU6SXNzdWUxMzQ3") == "graphql-node-id"
    assert id_space("acme/monorepo#1347") == "unknown"
    assert id_space(None) == "unknown"
    assert id_space(True) == "unknown"


def test_the_crosswalk_holds_in_both_directions():
    facts = crosswalk(REST_ISSUE, GQL_ISSUE)
    assert facts["node_ids_match"]
    assert facts["database_ids_match"]
    state, detail = classify_pair(REST_ISSUE, GQL_ISSUE)
    assert state == "crosswalk-confirmed"
    assert "REST node_id equals GraphQL id" in detail


def test_two_different_objects_are_not_reported_as_a_key_problem():
    state, detail = classify_pair(REST_ISSUE, {"id": "MDU6SXNzdWUx", "databaseId": 1})
    assert state == "crosswalk-broken"
    assert "not the same object" in detail
    assert "number is not its databaseId" in repair(state)


def test_a_type_with_no_database_id_has_only_one_key():
    state, _ = classify_pair(REST_ISSUE, {"id": NEW_STYLE, "databaseId": None})
    assert state == "database-id-absent"
    assert "node ID" in repair(state)
    assert classify_pair({}, {})[0] == "incomplete"


def test_a_column_holding_both_spaces_is_the_finding():
    state, detail = classify_store(["1347", "MDU6SXNzdWUxMzQ3", NEW_STYLE])
    assert state == "mixed-key-space"
    assert "1 database id(s)" in detail
    assert "2 node id(s)" in detail
    assert "pick one key space" in repair(state)


def test_a_consistent_column_is_left_alone():
    assert classify_store(["1347", "1348"])[0] == "consistent-database-id"
    assert classify_store(["MDU6SXNzdWUxMzQ3", NEW_STYLE])[0] == "consistent-node-id"
    assert classify_store(["acme/monorepo#1"])[0] == "unrecognised"
    assert classify_store([])[0] == "no-sample"


def test_the_join_returns_nothing_across_two_key_spaces():
    rest_side = ["1347", "1348"]
    graphql_side = ["MDU6SXNzdWUxMzQ3", "MDU6SXNzdWUxMzQ4"]
    assert join_rows(rest_side, graphql_side) == 0
    assert join_rows_normalised(rest_side, graphql_side) == 2


def test_normalising_cannot_rescue_the_new_format():
    assert join_rows_normalised(["1347"], ["MDU6SXNzdWUxMzQ3"]) == 1
    assert join_rows_normalised(["1347"], [NEW_STYLE]) == 0


def test_the_migration_is_split_into_offline_and_refetch():
    split = migration_split(["1347", "MDU6SXNzdWUxMzQ3", NEW_STYLE, "junk"])
    assert split == {"already_numeric": 1, "decodable_offline": 1,
                     "needs_refetching": 1}


def test_the_number_is_a_third_integer_and_not_the_database_id():
    other = {"id": 2136843289, "node_id": "MDU6SXNzdWUx", "number": 1347}
    assert number_is_not_the_database_id(other) is True
    assert number_is_not_the_database_id(REST_ISSUE) is False
    assert number_is_not_the_database_id({}) is None
    # Both are integers, so a column typed for one silently accepts the other.
    assert id_space(other["number"]) == id_space(other["id"])


def test_the_document_this_script_sends_is_a_read():
    assert operations("query Q { repository(owner: \"a\", name: \"b\") { id databaseId } }") == ["query"]
    assert refusal("mutation M { addStar(input: {}) { clientMutationId } }")
    assert refusal("subscription S { thing { id } }")
    assert refusal("") == "the document contains no operation to send."
