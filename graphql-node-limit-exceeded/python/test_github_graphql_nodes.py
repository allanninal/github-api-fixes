from github_graphql_nodes import (
    NODE_LIMIT, caveats, commas, connections, deepest, exceeds,
    fragment_spreads, node_count, operations, point_cost, refusal, repair,
    reported_node_count, rejected_for_nodes, reshape, unresolved, verdict,
)

THREE_LEVELS = """query {
  organization(login: "acme") {
    repositories(first: 100) {
      nodes {
        pullRequests(first: 100) {
          nodes {
            comments(first: 100) { nodes { id } }
          }
        }
      }
    }
  }
}"""

SMALL = """query {
  organization(login: "acme") {
    repositories(first: 100) {
      nodes { pullRequests(first: 10) { nodes { number } } }
    }
  }
}"""


def test_the_canonical_query_is_the_documented_million():
    assert node_count(THREE_LEVELS) == 1_010_100
    assert exceeds(node_count(THREE_LEVELS))
    assert NODE_LIMIT == 500_000


def test_the_multiplier_chain_is_what_makes_it_large():
    conns = connections(THREE_LEVELS)
    by_field = {c["field"]: c for c in conns}
    assert by_field["repositories"]["ancestors"] == 1
    assert by_field["repositories"]["nodes"] == 100
    assert by_field["pullRequests"]["ancestors"] == 100
    assert by_field["pullRequests"]["nodes"] == 10_000
    assert by_field["comments"]["ancestors"] == 10_000
    assert by_field["comments"]["nodes"] == 1_000_000


def test_the_deepest_connection_carries_almost_all_of_it():
    d = deepest(THREE_LEVELS)
    assert d["field"] == "comments"
    assert d["nodes"] == 1_000_000


def test_the_repair_is_a_number_and_the_number_fits():
    field, current, suggested = reshape(THREE_LEVELS)
    assert field == "comments"
    assert current == 100
    assert suggested == 48
    # The point of the suggestion is that taking it works.
    fixed = THREE_LEVELS.replace("comments(first: 100)", "comments(first: 48)")
    assert node_count(fixed) == 490_100
    assert not exceeds(node_count(fixed))


def test_a_query_that_cannot_be_rescued_by_one_number_says_so():
    huge = ("query { a(first: 100) { nodes { b(first: 100) { nodes { "
            "c(first: 100) { nodes { d(first: 100) { nodes { id } } } } } } } } }")
    assert exceeds(node_count(huge))
    field, _current, suggested = reshape(huge)
    assert suggested is None
    assert "split it into separate queries" in repair("over-node-limit", field, 100, None)


def test_a_small_query_is_not_flagged():
    assert node_count(SMALL) == 1_100
    state, detail = verdict(SMALL)
    assert state == "within-node-limit"
    assert "0%" in detail or "%" in detail


def test_the_verdict_names_the_three_bands():
    assert verdict(THREE_LEVELS)[0] == "over-node-limit"
    near = "query { a(first: 100) { nodes { b(first: 4500) { nodes { id } } } } }"
    assert node_count(near) == 450_100
    assert verdict(near)[0] == "near-node-limit"
    assert verdict(SMALL)[0] == "within-node-limit"
    assert verdict("query { viewer { login } }")[0] == "no-connections"


def test_a_slice_supplied_as_a_variable_is_resolved_or_reported():
    doc = "query($n: Int!) { a(first: $n) { nodes { id } } }"
    assert node_count(doc, {"n": 50}) == 50
    assert verdict(doc, {"n": 50})[0] == "within-node-limit"
    assert unresolved(doc) == ["a"]
    assert verdict(doc)[0] == "unresolved-variables"
    assert "Pass --variables" in caveats(doc)[0]


def test_a_directive_does_not_erase_the_slice_before_it():
    doc = ("query($show: Boolean!) { repositories(first: 100) @include(if: $show) "
           "{ nodes { id } } }")
    assert node_count(doc) == 100


def test_a_fragment_spread_makes_the_total_a_lower_bound():
    doc = ("query { repositories(first: 100) { nodes { ...RepoBits } } } "
           "fragment RepoBits on Repository { pullRequests(first: 100) { nodes { id } } }")
    assert fragment_spreads(doc) == ["RepoBits"]
    assert any("lower bound" in c for c in caveats(doc))


def test_an_inline_fragment_is_not_mistaken_for_a_spread():
    doc = "query { search(query: \"x\", type: ISSUE, first: 10) { nodes { ... on Issue { id } } } }"
    assert fragment_spreads(doc) == []
    assert node_count(doc) == 10


def test_the_word_first_inside_a_string_is_not_a_slice():
    doc = 'query { search(query: "first: 100", type: ISSUE, first: 5) { nodes { id } } }'
    assert node_count(doc) == 5


def test_a_comment_is_not_read_as_part_of_the_query():
    doc = "# repositories(first: 100)\nquery { a(first: 7) { nodes { id } } }"
    assert node_count(doc) == 7


def test_the_server_can_be_asked_to_agree_but_does_not_have_to_be():
    assert rejected_for_nodes({"errors": [{"type": "MAX_NODE_LIMIT_EXCEEDED"}]})
    assert not rejected_for_nodes({"errors": [{"type": "RATE_LIMITED"}]})
    assert reported_node_count({"data": {"rateLimit": {"nodeCount": 1100}}}) == 1100
    assert reported_node_count({"data": {"viewer": {"login": "ada"}}}) is None


def test_counts_are_printed_in_something_readable():
    assert commas(1_010_100) == "1,010,100"
    assert commas(100) == "100"
    assert commas(None) == "None"


def test_the_default_run_spends_nothing():
    assert point_cost(False) == 0
    assert point_cost(True) == 1


def test_the_script_refuses_to_analyse_and_send_a_mutation():
    assert operations(THREE_LEVELS) == ["query"]
    assert refusal("mutation M { addStar(input: {}) { clientMutationId } }")
    assert refusal(THREE_LEVELS) is None
