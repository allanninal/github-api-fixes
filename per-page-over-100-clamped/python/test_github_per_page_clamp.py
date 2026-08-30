from github_per_page_clamp import (
    MAX_PER_PAGE, clamped_to, is_over_maximum, parse_link, predicates_disagree,
    read_cost, repair, stops_on_missing_next, stops_on_short_page, verdict,
)

MORE = {"next": "https://api.github.com/repositories/1/issues?page=2"}
END = {"prev": "https://api.github.com/repositories/1/issues?page=3"}


def test_the_clamp_is_a_minimum_not_a_rejection():
    assert clamped_to(500) == MAX_PER_PAGE
    assert clamped_to(101) == MAX_PER_PAGE
    assert clamped_to(100) == 100
    assert clamped_to(30) == 30
    assert clamped_to("50") == 50


def test_a_page_size_that_is_not_one_is_reported_rather_than_guessed():
    assert clamped_to(0) is None
    assert clamped_to(-5) is None
    assert clamped_to(None) is None
    assert clamped_to("many") is None


def test_only_values_above_the_maximum_are_lowered():
    assert is_over_maximum(500)
    assert is_over_maximum(101)
    assert not is_over_maximum(100)
    assert not is_over_maximum(None)


def test_the_short_page_check_is_wrong_on_a_clamped_response():
    assert stops_on_short_page(500, 100)
    assert not stops_on_short_page(100, 100)
    assert stops_on_short_page(100, 42)


def test_the_header_check_does_not_care_about_page_sizes():
    assert not stops_on_missing_next(MORE)
    assert stops_on_missing_next(END)
    assert stops_on_missing_next({})
    assert stops_on_missing_next(None)


def test_the_finding_is_exactly_the_disagreement():
    assert predicates_disagree(500, 100, MORE)
    assert not predicates_disagree(500, 100, END)
    assert not predicates_disagree(100, 100, MORE)


def test_a_clamped_page_with_more_behind_it_is_the_finding():
    state, detail = verdict(500, 100, MORE)
    assert state == "clamped-and-truncated"
    assert "reduced to 100" in detail
    assert "stops on a short page" in detail


def test_a_collection_that_ends_on_the_boundary_is_still_a_trap():
    state, detail = verdict(500, 100, END)
    assert state == "clamped-at-boundary"
    assert "item 101" in detail


def test_a_small_collection_cannot_prove_the_clamp():
    state, detail = verdict(500, 12, {})
    assert state == "clamped-untested"
    assert "cannot be shown on this path" in detail


def test_an_endpoint_with_a_smaller_maximum_is_named_separately():
    state, detail = verdict(100, 50, MORE)
    assert state == "smaller-maximum"
    assert "smaller page than you requested" in detail


def test_a_full_page_within_the_cap_is_not_a_finding():
    assert verdict(100, 100, MORE)[0] == "within-cap-more-pages"
    assert verdict(100, 100, END)[0] == "within-cap-complete"
    assert verdict(30, 11, {})[0] == "within-cap-complete"


def test_an_unreadable_response_is_not_reported_as_a_clamp():
    assert verdict(500, None, MORE)[0] == "unknown"
    assert verdict(None, 100, MORE)[0] == "unknown"


def test_the_link_header_survives_a_comma_inside_a_url():
    header = ('<https://api.github.com/repos/o/n/issues?labels=bug,ci&page=2>; rel="next", '
              '<https://api.github.com/repos/o/n/issues?labels=bug,ci&page=9>; rel="last"')
    links = parse_link(header)
    assert set(links) == {"next", "last"}
    assert links["next"].endswith("page=2")
    assert parse_link(None) == {}


def test_the_repair_never_suggests_asking_for_more_than_the_maximum():
    for state in ("clamped-and-truncated", "clamped-at-boundary", "clamped-untested"):
        assert "per_page=100" in repair(state)
        assert "500" not in repair(state)
    assert "smaller page than 100" in repair("smaller-maximum")
    assert repair("within-cap-complete") == "nothing."


def test_the_run_says_what_it_will_spend():
    assert read_cost(["/a", "/b", "/c"]) == 3
    assert read_cost(["/a", "/b", "/c"], confirm=True) == 6
    assert read_cost([]) == 0
    assert read_cost(None) == 0
