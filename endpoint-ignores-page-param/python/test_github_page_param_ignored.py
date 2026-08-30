from github_page_param_ignored import (
    cursor_hint, identities, identity, link_params, link_style, loop_terminates,
    overlaps, parse_link, read_cost, repair, same_rows, verdict,
)

BASE = "https://api.github.com/repos/o/n"
OFFSET_LINK = {"next": BASE + "/issues?per_page=1&page=2"}
CURSOR_LINK = {"next": BASE + "/activity?per_page=1&after=Y3Vyc29yOjE="}
BEFORE_LINK = {"next": BASE + "/activity?per_page=1&before=Y3Vyc29yOjk="}
NO_LINK = {}


def test_the_link_style_is_read_from_the_parameter_names():
    assert link_style(CURSOR_LINK) == "cursor"
    assert link_style(BEFORE_LINK) == "cursor"
    assert link_style(OFFSET_LINK) == "offset"
    assert link_style(NO_LINK) == "none"
    assert link_style(None) == "none"


def test_the_cursor_parameter_is_named_so_the_repair_can_be_concrete():
    assert cursor_hint(CURSOR_LINK) == "after"
    assert cursor_hint(BEFORE_LINK) == "before"
    assert cursor_hint(OFFSET_LINK) is None
    assert link_params(OFFSET_LINK) == ["page", "per_page"]


def test_identifiers_fall_back_through_the_fields_a_list_might_use():
    assert identity({"id": 41, "node_id": "MDQ6"}) == "41"
    assert identity({"node_id": "MDQ6"}) == "MDQ6"
    assert identity({"sha": "9f2c1ab"}) == "9f2c1ab"
    assert identity({"url": BASE + "/pulls/3"}) == BASE + "/pulls/3"
    assert identity({"title": "no identifier here"}) is None
    assert identity(None) is None


def test_a_page_of_unidentifiable_items_does_not_become_a_finding():
    assert identities([{"title": "a"}, {"title": "b"}]) == []
    assert identities([{"id": 1}, {"title": "b"}]) == ["1"]
    assert identities("not a list") == []


def test_identical_rows_with_a_cursor_link_is_the_definite_finding():
    state, detail = verdict("cursor", ["9"], ["9"])
    assert state == "ignores-page"
    assert "does not read page at all" in detail
    assert "no terminating condition" in detail
    assert not loop_terminates(state)


def test_identical_rows_with_a_page_link_is_only_a_suspicion():
    state, detail = verdict("offset", ["9"], ["9"])
    assert state == "suspect-ignores-page"
    assert "may be a feed that moved" in detail
    assert "Re-run it" in detail


def test_a_partial_overlap_is_its_own_answer():
    state, detail = verdict("offset", ["9", "8"], ["8", "7"])
    assert state == "overlapping-pages"
    assert "unstable sort" in detail
    assert loop_terminates(state)


def test_a_cursor_endpoint_that_pages_properly_is_not_a_finding():
    state, detail = verdict("cursor", ["9"], ["8"])
    assert state == "cursor-pagination"
    assert "not by number" in detail


def test_offset_pagination_that_works_is_reported_as_working():
    assert verdict("offset", ["9"], ["8"])[0] == "offset-honoured"
    assert verdict("offset", ["9"], [])[0] == "offset-honoured"


def test_an_empty_first_page_proves_nothing():
    state, detail = verdict("none", [], [])
    assert state == "inconclusive-empty"
    assert "no comparison to make" in detail


def test_the_row_comparisons_are_order_sensitive_and_set_based_in_turn():
    assert same_rows(["1", "2"], ["1", "2"])
    assert not same_rows(["1", "2"], ["2", "1"])
    assert not same_rows([], [])
    assert overlaps(["1", "2"], ["2", "3"])
    assert not overlaps(["1"], ["2"])


def test_the_repair_names_the_cursor_the_endpoint_actually_uses():
    assert "after=" in repair("ignores-page", CURSOR_LINK)
    assert "before=" in repair("ignores-page", BEFORE_LINK)
    assert "no next page" in repair("ignores-page", NO_LINK)
    assert "Re-run" not in repair("cursor-pagination")


def test_the_repair_for_a_suspicion_changes_no_code():
    fix = repair("suspect-ignores-page")
    assert "re-run the check" in fix
    assert "before changing any code" in fix


def test_the_header_is_parsed_around_commas_inside_urls():
    header = '<%s/issues?labels=bug,ci&page=2>; rel="next"' % BASE
    assert link_style(parse_link(header)) == "offset"


def test_the_run_says_what_it_will_spend():
    assert read_cost(["/a", "/b"]) == 4
    assert read_cost([]) == 0
    assert read_cost(None) == 0
