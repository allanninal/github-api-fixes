from github_graphql_cost import (
    POINTS_PER_QUERY, blank_noise, classify, drift, gap, inject_rate_limit,
    measured_cost, measured_nodes, operations, point_cost, points_per_hour,
    predicted_cost, refusal, repair, returned_nodes, selection_set_start,
    slice_values,
)

QUERY = ("query($login: String!) { repositoryOwner(login: $login) {"
         " repositories(first: 50) { nodes { name"
         " issues(first: 20) { nodes { number } } } } } }")

BODY = {"data": {
    "rateLimit": {"cost": 14, "nodeCount": 3180, "limit": 5000, "remaining": 4986},
    "repositoryOwner": {"repositories": {"nodes": [
        {"name": "a", "issues": {"nodes": [{"number": 1}, {"number": 2}]}},
        {"name": "b", "issues": {"nodes": [{"number": 3}]}},
    ]}}}}


def test_the_injection_lands_in_the_operations_selection_set():
    out = inject_rate_limit(QUERY)
    assert "rateLimit { cost nodeCount limit remaining resetAt }" in out
    at = out.index("rateLimit")
    assert out.index("repositoryOwner") > at
    assert out.count("rateLimit") == 1


def test_a_document_that_already_asks_for_it_is_left_alone():
    once = inject_rate_limit(QUERY)
    assert inject_rate_limit(once) == once
    already = "query { rateLimit { cost } viewer { login } }"
    assert inject_rate_limit(already) == already


def test_a_brace_in_the_variable_definitions_is_not_the_selection_set():
    doc = 'query($order: IssueOrder = {field: CREATED_AT}) { viewer { login } }'
    at = selection_set_start(doc)
    assert doc[at:at + 3] == "{ v"
    out = inject_rate_limit(doc)
    assert out.index("IssueOrder") < out.index("rateLimit") < out.index("viewer")


def test_blanking_the_noise_keeps_every_index_where_it_was():
    doc = 'query { search(query: "a { b }", type: ISSUE, first: 5) { issueCount } }'
    blanked = blank_noise(doc)
    assert len(blanked) == len(doc)
    assert "{ b }" not in blanked
    assert blanked.index("issueCount") == doc.index("issueCount")


def test_the_prediction_comes_from_the_slices_and_never_from_zero():
    assert predicted_cost(QUERY, {}) == (1, 0)
    big = "query { a(first: 100) { nodes { b(first: 100) { nodes { id } } } } }"
    assert predicted_cost(big, {})[0] == 2
    assert predicted_cost("query { viewer { login } }", {}) == (1, 0)


def test_an_unresolved_slice_makes_the_prediction_a_lower_bound():
    doc = "query($n: Int!) { a(first: $n) { nodes { id } } }"
    points, unresolved = predicted_cost(doc, {})
    assert unresolved == 1
    assert points == 1
    assert predicted_cost(doc, {"n": 300})[0] == 3


def test_a_variable_definition_is_not_counted_as_a_slice():
    doc = "query($first: Int = 250) { a(first: 10) { nodes { id } } }"
    assert [(v["arg"], v["value"]) for v in slice_values(doc, {})] == [("first", 10)]


def test_the_server_number_is_read_out_of_the_response_wherever_it_sits():
    assert measured_cost(BODY) == 14
    assert measured_nodes(BODY) == 3180
    assert measured_cost({"data": {"viewer": {"login": "x"}}}) is None
    assert measured_cost(None) is None


def test_the_price_is_compared_with_the_data_that_came_back():
    assert returned_nodes(BODY["data"]) == 5
    assert returned_nodes({"nodes": [1, 2, 3]}) == 3
    assert returned_nodes({"name": "a"}) == 0


def test_the_gap_between_the_text_and_the_server_is_the_finding():
    assert gap(3, 14)[1] == "far-above-the-text"
    assert gap(4, 6)[1] == "above-the-text"
    assert gap(4, 4)[1] == "close-to-the-text"
    assert gap(10, 2)[1] == "below-the-text"
    assert gap(3, None)[1] == "unmeasured"


def test_drift_against_a_recorded_baseline_is_reported_as_a_percentage():
    state, detail = drift(3, 14)
    assert state == "increased"
    assert "367%" in detail
    assert drift(3, 3)[0] == "unchanged"
    assert drift(14, 3)[0] == "decreased"
    assert drift(None, 14)[0] == "no-baseline"


def test_a_price_rise_outranks_everything_else_because_it_is_reviewable():
    state, detail = classify(14, 3, 3, 5)
    assert state == "cost-increased-since-the-baseline"
    assert "367%" in detail
    assert "code review" in repair(state)


def test_a_query_costing_more_than_its_text_suggests_is_named_as_that():
    state, detail = classify(14, 3, None, 5)
    assert state == "cost-above-the-shape-of-the-query"
    assert "factor of 4.7" in detail


def test_cost_not_following_the_data_is_its_own_finding():
    state, detail = classify(9, 9, 9, 4)
    assert state == "cost-unrelated-to-the-data-returned"
    assert "4 node(s) came back for 9 point(s)" in detail
    assert "Filters change" in repair(state)


def test_an_unmeasured_run_says_so_rather_than_guessing():
    state, _detail = classify(None, 3, 3, 5)
    assert state == "cost-unmeasured"
    assert "rateLimit { cost nodeCount remaining }" in repair(state)


def test_the_hourly_projection_is_multiplication_and_nothing_more():
    assert points_per_hour(14, 240) == 3360
    assert points_per_hour(14, 0) is None
    assert points_per_hour(None, 240) is None


def test_the_script_refuses_to_send_a_mutation():
    assert operations("query Q { viewer { login } }") == ["query"]
    assert refusal("mutation M { addStar(input: {}) { clientMutationId } }")
    assert refusal("subscription S { thing { id } }")
    assert refusal(QUERY) is None


def test_the_run_says_what_it_will_spend():
    assert POINTS_PER_QUERY == 1
    assert point_cost(1) == 1
    assert point_cost(0) == 0
