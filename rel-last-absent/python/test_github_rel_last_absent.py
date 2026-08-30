from github_rel_last_absent import (
    capabilities, item_count, naive_page_count, page_count, page_param,
    pagination_style, parse_link, read_cost, rels, repair, unavailable, verdict,
)

BASE = "https://api.github.com/repositories/1/issues"
INDEXABLE = {"next": BASE + "?page=2", "last": BASE + "?page=912"}
WALK_ONLY = {"next": BASE + "?page=2"}
DEEP = {"first": BASE + "?page=1", "prev": BASE + "?page=4",
        "next": BASE + "?page=6", "last": BASE + "?page=40"}
SINGLE = {}


def test_the_classification_is_three_states_not_two():
    assert pagination_style(INDEXABLE) == "indexable"
    assert pagination_style(WALK_ONLY) == "walk-only"
    assert pagination_style(SINGLE) == "single-page"
    assert pagination_style(None) == "single-page"


def test_walk_only_is_never_mistaken_for_a_single_page():
    assert pagination_style(WALK_ONLY) != pagination_style(SINGLE)
    assert capabilities("walk-only") != capabilities("single-page")


def test_a_careful_page_count_refuses_to_answer_without_the_field():
    assert page_count(INDEXABLE) == 912
    assert page_count(DEEP) == 40
    assert page_count(SINGLE) == 1
    assert page_count(WALK_ONLY) is None


def test_the_careless_page_count_turns_the_gap_into_one():
    assert naive_page_count(WALK_ONLY) == 1
    assert naive_page_count(INDEXABLE) == 912
    assert naive_page_count(WALK_ONLY) != page_count(WALK_ONLY)
    assert naive_page_count(INDEXABLE) == page_count(INDEXABLE)


def test_the_item_count_is_only_offered_at_a_page_size_of_one():
    assert item_count(INDEXABLE, 1) == 912
    assert item_count(INDEXABLE, 100) is None
    assert item_count(WALK_ONLY, 1) is None


def test_the_capability_table_says_what_a_pager_may_rely_on():
    walk = capabilities("walk-only")
    assert walk["walk"] is True
    assert walk["page_count"] is False
    assert walk["progress_bar"] is False
    assert walk["parallel_fanout"] is False
    assert walk["jump_to_last"] is False
    assert capabilities("indexable")["parallel_fanout"] is True


def test_the_capability_table_is_a_copy_so_a_caller_cannot_edit_it():
    capabilities("indexable")["page_count"] = False
    assert capabilities("indexable")["page_count"] is True


def test_the_broken_patterns_are_named_in_a_fixed_order():
    assert unavailable("walk-only") == ["page count", "progress bar",
                                        "parallel fan-out", "jump to last"]
    assert unavailable("indexable") == []
    assert unavailable("single-page") == ["parallel fan-out", "jump to last"]


def test_the_walk_only_verdict_prints_the_number_that_moves_somebody():
    state, detail = verdict(WALK_ONLY)
    assert state == "walk-only"
    assert "only knowable by walking it" in detail
    assert "reports 1 page" in detail


def test_an_indexable_endpoint_is_reported_as_a_snapshot():
    state, detail = verdict(INDEXABLE, 1)
    assert state == "indexable"
    assert "912" in detail
    assert "moves between calls" in detail


def test_a_single_page_list_is_not_a_pagination_finding():
    state, detail = verdict(SINGLE)
    assert state == "single-page"
    assert "nothing about paging applies" in detail
    assert repair(state) == "nothing."


def test_the_page_parameter_is_read_out_of_the_url_defensively():
    assert page_param(BASE + "?page=7&per_page=1") == 7
    assert page_param(BASE + "?per_page=1") is None
    assert page_param("") is None
    assert page_param(None) is None


def test_the_header_is_parsed_around_commas_inside_urls():
    header = ('<%s?labels=bug,ci&page=2>; rel="next", '
              '<%s?labels=bug,ci&page=9>; rel="last"' % (BASE, BASE))
    assert rels(parse_link(header)) == ["last", "next"]
    assert parse_link("") == {}


def test_the_repair_for_walk_only_never_asks_for_a_page_count():
    fix = repair("walk-only")
    assert 'rel="next"' in fix
    assert "never require" in fix
    assert "cache it as the size of the job" in repair("indexable")


def test_the_run_says_what_it_will_spend():
    assert read_cost(["/a", "/b", "/c", "/d", "/e"]) == 5
    assert read_cost([]) == 0
    assert read_cost(None) == 0
