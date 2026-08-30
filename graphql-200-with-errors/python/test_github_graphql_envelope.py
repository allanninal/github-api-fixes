from github_graphql_envelope import (
    POINTS_PER_QUERY, behaviour_for, classify, envelope_says_ok, error_types,
    has_usable_data, operations, point_cost, predicates_disagree, refusal,
    repair, status_says_ok,
)

FAILED = {"data": {"repository": None},
          "errors": [{"type": "NOT_FOUND", "message": "Could not resolve to a Repository"}]}
PARTIAL = {"data": {"repository": {"name": "monorepo", "diskUsage": None}},
           "errors": [{"type": "FORBIDDEN", "path": ["repository", "diskUsage"]}]}
CLEAN = {"data": {"repository": {"name": "monorepo"}}}


def test_the_status_line_says_success_on_a_failed_query():
    assert status_says_ok(200)
    assert status_says_ok("201")
    assert not status_says_ok(403)
    assert not status_says_ok(None)


def test_the_envelope_check_reads_the_body_instead():
    assert not envelope_says_ok(FAILED)
    assert not envelope_says_ok(PARTIAL)
    assert envelope_says_ok(CLEAN)
    assert envelope_says_ok({"data": {}, "errors": []})
    assert not envelope_says_ok("not a body")


def test_the_finding_is_exactly_the_disagreement():
    assert predicates_disagree(200, FAILED)
    assert predicates_disagree(200, PARTIAL)
    assert not predicates_disagree(200, CLEAN)
    assert not predicates_disagree(502, FAILED)


def test_error_types_survive_an_entry_with_no_type():
    assert error_types(FAILED) == ["NOT_FOUND"]
    assert error_types({"errors": [{"message": "boom"}]}) == ["UNTYPED"]
    assert error_types({"errors": ["a string"]}) == ["UNTYPED"]
    assert error_types(CLEAN) == []


def test_usable_data_means_at_least_one_field_resolved():
    assert not has_usable_data(FAILED)
    assert has_usable_data(PARTIAL)
    assert has_usable_data(CLEAN)
    assert not has_usable_data({"data": None, "errors": [{"type": "RATE_LIMITED"}]})


def test_a_200_carrying_errors_and_no_data_is_the_headline():
    state, detail = classify(200, FAILED)
    assert state == "200-with-errors-no-data"
    assert "NOT_FOUND" in detail
    assert "read body.errors before body.data" in repair(state)


def test_errors_alongside_real_data_are_handed_on_rather_than_absorbed():
    state, detail = classify(200, PARTIAL)
    assert state == "200-with-errors-and-data"
    assert "partial success" in detail
    assert "graphql-partial-data-nulls" in repair(state)
    assert "do not retry" in repair(state)


def test_a_real_transport_failure_is_not_this_note():
    state, _detail = classify(502, {"errors": [{"type": "INTERNAL"}]})
    assert state == "transport-failure"
    assert "status code as you already do" in repair(state)


def test_a_clean_response_is_not_reported_as_proof_of_anything():
    state, detail = classify(200, CLEAN)
    assert state == "200-clean"
    assert "agreement rather than proof" in detail


def test_an_unreadable_body_is_not_reported_as_success():
    assert classify(200, None)[0] == "unreadable"
    assert classify(200, [1, 2])[0] == "unreadable"


def test_each_error_type_gets_its_own_behaviour():
    assert behaviour_for("RATE_LIMITED")[0] == "wait"
    assert behaviour_for("FORBIDDEN")[0] == "alert"
    assert behaviour_for("NOT_FOUND")[0] == "record-absent"
    assert behaviour_for("MAX_NODE_LIMIT_EXCEEDED")[0] == "reshape"
    assert behaviour_for("INTERNAL")[0] == "retry-once"


def test_a_node_limit_error_is_never_advised_to_retry():
    action, detail = behaviour_for("MAX_NODE_LIMIT_EXCEEDED")
    assert action == "reshape"
    assert "fail identically every time" in detail


def test_an_unknown_error_type_falls_through_rather_than_being_guessed():
    action, detail = behaviour_for("SOMETHING_NEW_IN_2027")
    assert action == "log-verbatim"
    assert "does not know" in detail


def test_the_script_refuses_to_send_a_mutation():
    assert operations("query Q { viewer { login } }") == ["query"]
    assert operations("{ viewer { login } }") == ["query"]
    assert operations("mutation M { addStar(input: {}) { clientMutationId } }") == ["mutation"]
    assert refusal("mutation M { addStar(input: {}) { clientMutationId } }")
    assert refusal("subscription S { thing { id } }")
    assert refusal("") == "the document contains no operation to send."
    assert refusal("query Q { viewer { login } }") is None


def test_the_word_mutation_inside_a_string_is_not_a_mutation():
    doc = 'query Q { search(query: "mutation", type: ISSUE, first: 1) { issueCount } }'
    assert operations(doc) == ["query"]
    assert refusal(doc) is None


def test_a_commented_out_mutation_is_not_sent_and_not_feared():
    doc = "# mutation M { addStar }\nquery Q { viewer { login } }"
    assert operations(doc) == ["query"]
    assert refusal(doc) is None


def test_the_run_says_what_it_will_spend():
    assert POINTS_PER_QUERY == 1
    assert point_cost([1, 2]) == 2
    assert point_cost([]) == 0
    assert point_cost(None) == 0
