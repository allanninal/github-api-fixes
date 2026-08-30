from github_graphql_slice import (
    CEILING, POINTS_PER_QUERY, argument_value, audit, classify, error_phase,
    offending_argument, operations, pages_needed, point_cost, refusal, repair,
    resolve_slice, slicing_arguments, variable_defaults, verdict,
)

LITERAL = "query { repository(owner: \"a\", name: \"b\") { issues(first: 500) { totalCount } } }"
VIA_DEFAULT = ("query($first: Int = 250) { repository(owner: \"a\", name: \"b\")"
               " { issues(first: $first) { totalCount } } }")
SAFE = ("query($first: Int = 100) { repository(owner: \"a\", name: \"b\")"
        " { issues(first: $first) { totalCount } } }")

VALIDATION_BODY = {"errors": [{"message": "Argument 'first' on Field 'issues' has an "
                                          "invalid value (500). Expected type 'Int'."}]}
EXECUTION_BODY = {"data": {"repository": None},
                  "errors": [{"type": "NOT_FOUND", "message": "Could not resolve"}]}


def test_the_ceiling_is_one_hundred_everywhere():
    assert CEILING == 100
    assert verdict(101) == "over-ceiling"
    assert verdict(100) == "at-ceiling"
    assert verdict(1) == "under-ceiling"
    assert verdict(0) == "below-one"
    assert verdict(None) == "unresolved"


def test_a_literal_over_the_ceiling_is_found_in_the_text():
    found = audit(LITERAL, {})
    assert [(f["field"], f["arg"], f["value"], f["source"]) for f in found] == [
        ("issues", "first", 500, "literal")]
    state, detail = classify(found)
    assert state == "over-ceiling-in-the-document"
    assert "500" in detail


def test_a_variable_default_over_the_ceiling_is_invisible_to_a_grep():
    assert "250" not in "".join(f["written"] for f in audit(VIA_DEFAULT, {}))
    found = audit(VIA_DEFAULT, {})
    assert found[0]["value"] == 250
    assert found[0]["source"] == "variable-default"
    state, detail = classify(found)
    assert state == "over-ceiling-through-a-variable"
    assert "finds nothing" in detail


def test_a_supplied_variable_beats_the_default_because_the_server_sees_it():
    found = audit(SAFE, {"first": 400})
    assert found[0]["value"] == 400
    assert found[0]["source"] == "variable-supplied"
    assert classify(found)[0] == "over-ceiling-through-a-variable"
    assert classify(audit(SAFE, {}))[0] == "within-the-ceiling"


def test_an_unresolved_variable_is_never_assumed_safe():
    doc = "query($n: Int!) { repository(owner: \"a\", name: \"b\") { issues(first: $n) { totalCount } } }"
    found = audit(doc, {})
    assert found[0]["source"] == "unresolved"
    assert found[0]["verdict"] == "unresolved"
    state = classify(found)[0]
    assert state == "unresolved-slice"
    assert "--variables" in repair(state)


def test_a_variable_definition_is_not_an_argument_called_first():
    assert variable_defaults(VIA_DEFAULT) == {"$first": "250"}
    args = slicing_arguments(VIA_DEFAULT)
    assert len(args) == 1
    assert args[0]["field"] == "issues"
    assert argument_value("$first: Int = 250", "first") is None
    assert argument_value("first: 100, states: OPEN", "first") == "100"


def test_last_is_treated_exactly_like_first():
    doc = "query { repository(owner: \"a\", name: \"b\") { issues(last: 250) { totalCount } } }"
    found = audit(doc, {})
    assert found[0]["arg"] == "last"
    assert classify(found)[0] == "over-ceiling-in-the-document"


def test_the_word_first_inside_a_string_is_not_an_argument():
    doc = 'query { search(query: "first: 500", type: ISSUE, first: 10) { issueCount } }'
    found = audit(doc, {})
    assert [(f["arg"], f["value"]) for f in found] == [("first", 10)]
    assert classify(found)[0] == "within-the-ceiling"


def test_a_clean_document_is_sent_on_to_the_node_count_rather_than_cleared():
    state = classify(audit(SAFE, {}))[0]
    assert state == "within-the-ceiling"
    assert "graphql-node-limit-exceeded" in repair(state)


def test_the_pages_that_number_really_means():
    assert pages_needed(500) == 5
    assert pages_needed(101) == 2
    assert pages_needed(100) == 1
    assert pages_needed(0) is None
    assert pages_needed(None) is None


def test_a_validation_failure_carries_no_data_key_at_all():
    assert error_phase(200, VALIDATION_BODY) == "validation"
    assert error_phase(200, EXECUTION_BODY) == "execution"
    assert error_phase(200, {"data": {"repository": {"name": "x"}}}) == "clean"
    assert error_phase(200, None) == "unreadable"


def test_the_server_names_the_argument_and_the_field():
    assert offending_argument(VALIDATION_BODY) == ("first", "issues")
    assert offending_argument(EXECUTION_BODY) == (None, None)
    assert offending_argument(None) == (None, None)


def test_the_script_refuses_to_send_a_mutation():
    assert operations("query Q { viewer { login } }") == ["query"]
    assert refusal("mutation M { addStar(input: {}) { clientMutationId } }")
    assert refusal("subscription S { thing { id } }")
    assert refusal("") == "the document contains no operation to send."
    assert refusal(LITERAL) is None


def test_the_offline_audit_spends_nothing():
    assert POINTS_PER_QUERY == 1
    assert point_cost(False) == 0
    assert point_cost(True) == 1
