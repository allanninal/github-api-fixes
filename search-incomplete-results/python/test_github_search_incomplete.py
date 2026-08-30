from github_search_incomplete import (
    RESULT_CAP, SEARCH_BUCKET, above_result_cap, cacheable, counts_stable,
    flagged, item_count, max_total, narrowing, observe, qualifiers, read_cost,
    repair, retry_or_narrow, summarise, total_of, verdict, within_search_bucket,
)

PARTIAL = {"total_count": 412, "incomplete_results": True,
           "items": [{"id": 1}, {"id": 2}]}
WHOLE = {"total_count": 412, "incomplete_results": False,
         "items": [{"id": 1}, {"id": 2}, {"id": 3}]}


def test_the_flag_is_read_strictly_rather_than_truthily():
    assert flagged(PARTIAL)
    assert not flagged(WHOLE)
    assert not flagged({"incomplete_results": "true"})
    assert not flagged({"incomplete_results": 1})
    assert not flagged({})
    assert not flagged(None)


def test_the_three_kept_fields_survive_a_malformed_payload():
    assert observe(PARTIAL) == {"incomplete": True, "total": 412, "items": 2}
    assert total_of({"total_count": "412"}) == 412
    assert total_of({"total_count": None}) is None
    assert total_of([]) is None
    assert item_count({"items": None}) == 0
    assert item_count(None) == 0


def test_a_flagged_response_may_never_be_cached():
    assert not cacheable(PARTIAL)
    assert cacheable(WHOLE)
    assert not cacheable(None)


def test_every_round_partial_is_narrowed_not_retried():
    obs = [observe(PARTIAL)] * 3
    state, detail = verdict(obs)
    assert state == "timed-out-always"
    assert "No retry policy will fix that" in detail
    assert retry_or_narrow(obs) == "narrow"


def test_some_rounds_partial_is_retried_not_narrowed():
    obs = [observe(PARTIAL), observe(WHOLE), observe(WHOLE)]
    state, detail = verdict(obs)
    assert state == "timed-out-intermittent"
    assert "1 of 3" in detail
    assert retry_or_narrow(obs) == "retry"


def test_the_thousand_result_ceiling_is_ruled_out_by_name():
    detail = verdict([observe(PARTIAL)] * 2)[1]
    assert "1000-result ceiling" in detail
    assert "not the explanation" in detail


def test_a_query_over_the_ceiling_is_reported_as_two_problems():
    big = dict(PARTIAL, total_count=24831)
    state, detail = verdict([observe(big)] * 2)
    assert state == "timed-out-and-capped"
    assert "two separate problems" in detail
    assert retry_or_narrow([observe(big)] * 2) == "narrow"


def test_a_moving_count_with_no_flag_is_still_caught():
    obs = [observe(WHOLE), observe(dict(WHOLE, items=[{"id": 1}]))]
    state, detail = verdict(obs)
    assert state == "unstable-counts"
    assert "no round was flagged" in detail
    assert retry_or_narrow(obs) == "retry"


def test_three_clean_stable_rounds_are_not_a_finding():
    obs = [observe(WHOLE)] * 3
    assert verdict(obs)[0] == "complete"
    assert retry_or_narrow(obs) == "nothing"
    assert counts_stable(obs)


def test_no_rounds_is_not_reported_as_a_clean_result():
    assert verdict([])[0] == "no-observations"
    assert summarise([]) == {"rounds": 0, "flagged": 0, "item_counts": [], "totals": []}
    assert max_total([]) is None


def test_the_ceiling_predicate_is_strictly_above_the_cap():
    assert above_result_cap(RESULT_CAP + 1)
    assert not above_result_cap(RESULT_CAP)
    assert not above_result_cap(None)


def test_the_query_is_read_for_the_qualifiers_it_already_has():
    assert qualifiers("is:issue repo:acme/api label:bug") == {"is", "repo", "label"}
    assert qualifiers("-org:acme is:open") == {"org", "is"}
    assert qualifiers("") == set()
    assert qualifiers(None) == set()


def test_narrowing_suggests_only_what_is_missing():
    assert narrowing("is:issue state:open") == [
        "repo: or org:", "created: or updated: date range", "language:"]
    assert narrowing("org:acme created:>2026-01-01 language:go") == []
    assert narrowing("repo:acme/api updated:>2026-01-01") == ["language:"]


def test_the_repair_tells_a_hopeless_query_not_to_retry():
    fix = repair("timed-out-always", "is:issue state:open")
    assert "narrow the query" in fix
    assert "repo: or org:" in fix
    assert "Retrying will" in fix
    assert "never cache it" in repair("timed-out-intermittent", "is:issue")


def test_the_check_refuses_a_plan_that_would_not_fit_the_search_bucket():
    assert read_cost(["q"], 3) == 3
    assert read_cost(["a", "b"], 4) == 8
    assert read_cost([], 3) == 0
    assert within_search_bucket(3)
    assert within_search_bucket(SEARCH_BUCKET)
    assert not within_search_bucket(SEARCH_BUCKET + 1)
    assert not within_search_bucket(0)
