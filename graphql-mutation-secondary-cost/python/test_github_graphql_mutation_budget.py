from github_graphql_mutation_budget import (
    PROBE_QUERY, SECONDARY_POINTS_PER_MINUTE, WEIGHT_WITHOUT_MUTATION,
    WEIGHT_WITH_MUTATION, ceiling_per_minute, classify_rate, classify_throttle,
    min_gap_seconds, minutes_for_batch, operations, points_per_minute, price,
    refusal, repair, weight,
)

READ = "query Q($n: Int!) { repository(owner: \"a\", name: \"b\") { issues(first: $n) { nodes { id } } } }"
WRITE = "mutation M($id: ID!) { addLabelsToLabelable(input: {labelableId: $id, labelIds: []}) { clientMutationId } }"
THREE_WRITES = ("mutation A { one { clientMutationId } } "
                "mutation B { two { clientMutationId } } "
                "mutation C { three { clientMutationId } }")


def test_a_mutation_document_is_five_points_and_a_query_is_one():
    assert weight(WRITE) == WEIGHT_WITH_MUTATION == 5
    assert weight(READ) == WEIGHT_WITHOUT_MUTATION == 1


def test_the_weight_is_per_request_not_per_mutation():
    assert operations(THREE_WRITES) == ["mutation", "mutation", "mutation"]
    assert weight(THREE_WRITES) == 5
    # Which is why batching is a real reduction: three separate requests would
    # be 15 points, the same three in one document are 5.
    assert weight(WRITE) * 3 > weight(THREE_WRITES)


def test_the_word_mutation_in_a_string_or_a_comment_is_not_one():
    quoted = 'query Q { search(query: "mutation", type: ISSUE, first: 1) { issueCount } }'
    assert weight(quoted) == 1
    assert refusal(quoted) is None
    commented = "# mutation M { addStar }\nquery Q { viewer { login } }"
    assert weight(commented) == 1
    assert refusal(commented) is None


def test_the_ceiling_is_the_limit_divided_by_the_weight():
    assert SECONDARY_POINTS_PER_MINUTE == 2000
    assert ceiling_per_minute(5) == 400
    assert ceiling_per_minute(1) == 2000
    assert ceiling_per_minute(0) == 0
    assert ceiling_per_minute(None) == 0


def test_the_gap_falls_out_of_the_ceiling():
    assert round(min_gap_seconds(5), 3) == 0.15
    assert round(min_gap_seconds(1), 3) == 0.03
    assert min_gap_seconds(0) == 0.0


def test_a_rate_is_priced_in_points_not_in_requests():
    assert points_per_minute(500, 5) == 2500
    assert points_per_minute(500, 1) == 500
    assert points_per_minute(0, 5) == 0
    assert points_per_minute(None, 5) == 0


def test_the_same_rate_breaks_the_writer_and_not_the_reader():
    write_state, _ = classify_rate(500, weight(WRITE))
    read_state, _ = classify_rate(500, weight(READ))
    assert write_state == "over-ceiling"
    assert read_state == "within-ceiling"


def test_a_rate_just_inside_the_limit_is_still_reported():
    state, detail = classify_rate(340, 5)
    assert state == "near-ceiling"
    assert "1700" in detail
    assert "headroom" in repair(state)


def test_an_unmeasured_rate_is_priced_but_not_judged():
    state, detail = classify_rate(0, 5)
    assert state == "not-measured"
    assert "400" in detail


def test_a_secondary_message_with_a_healthy_budget_is_the_finding():
    state, detail = classify_throttle(
        403, "You have exceeded a secondary rate limit", 4863)
    assert state == "secondary-not-budget"
    assert "4863" in detail
    assert "points a minute" in repair(state)


def test_a_secondary_message_with_an_empty_budget_is_not_conclusive():
    state, _ = classify_throttle(
        429, "You have exceeded a secondary rate limit", 0)
    assert state == "secondary-limit"


def test_an_exhausted_hourly_budget_is_handed_to_the_other_note():
    state, _ = classify_throttle(200, "API rate limit exceeded", 0)
    assert state == "primary-exhausted"
    assert "graphql-rate-limited" in repair(state)


def test_a_403_that_is_not_a_throttle_is_not_called_one():
    state, _ = classify_throttle(403, "Resource not accessible", 4900)
    assert state == "forbidden-not-throttled"
    assert classify_throttle("", "", 4900)[0] == "no-throttle"


def test_a_batch_is_costed_in_minutes():
    assert minutes_for_batch(11000, 400) == 28
    assert minutes_for_batch(11000, 2000) == 6
    assert minutes_for_batch(11000, 0) is None


def test_the_document_is_priced_and_refused_in_the_same_breath():
    p = price("label_issue.graphql", WRITE, 500)
    assert p["points_per_request"] == 5
    assert p["ceiling_per_minute"] == 400
    assert p["state"] == "over-ceiling"
    assert p["not_sent"] and "does not send them" in p["not_sent"]
    assert price("fetch.graphql", READ, 500)["not_sent"] is None


def test_the_scripts_own_probe_passes_its_own_guard():
    assert refusal(PROBE_QUERY) is None
    assert weight(PROBE_QUERY) == 1
    assert refusal("subscription S { thing { id } }")
    assert refusal("") == "the document contains no operation to send."
