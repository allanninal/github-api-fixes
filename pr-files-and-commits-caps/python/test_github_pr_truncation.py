from github_pr_truncation import (
    CAPS, DEFAULT_PER_PAGE, MAX_PER_PAGE, beyond_cap, bounds_from_last, cap_for,
    counter_outside_bounds, last_page_from, one_page_shortfall, page_of,
    pages_needed, parse_link, read_cost, reachable, repair, verdict,
)


def test_the_two_ceilings_are_different_numbers():
    assert cap_for("files") == 3000
    assert cap_for("commits") == 250
    assert cap_for("comments") is None
    assert CAPS["files"] > CAPS["commits"]


def test_pages_needed_rounds_up_and_refuses_nonsense():
    assert pages_needed(3000, 100) == 30
    assert pages_needed(901, 100) == 10
    assert pages_needed(1, 100) == 1
    assert pages_needed(0, 100) == 0
    assert pages_needed(250, 30) == 9
    assert pages_needed(10, 0) is None
    assert pages_needed(None, 100) is None


def test_what_is_reachable_stops_at_the_ceiling():
    assert reachable("files", 4200) == 3000
    assert reachable("files", 12) == 12
    assert reachable("commits", 812) == 250
    assert reachable("files", None) is None


def test_what_is_beyond_the_ceiling_is_counted_exactly():
    assert beyond_cap("files", 4200) == 1200
    assert beyond_cap("files", 3000) == 0
    assert beyond_cap("commits", 251) == 1
    assert beyond_cap("commits", None) == 0


def test_a_last_page_implies_a_band_rather_than_a_number():
    assert bounds_from_last(3, 100) == (201, 300)
    assert bounds_from_last(1, 100) == (1, 100)
    assert bounds_from_last(0, 100) is None
    assert bounds_from_last(None, 100) is None
    assert bounds_from_last(3, 0) is None


def test_the_counter_is_only_wrong_when_it_leaves_the_band():
    assert counter_outside_bounds(150, (1, 100))
    assert counter_outside_bounds(0, (1, 100))
    assert not counter_outside_bounds(100, (1, 100))
    assert not counter_outside_bounds(250, (201, 300))
    assert not counter_outside_bounds(150, None)


def test_one_default_page_is_where_most_of_it_is_lost():
    assert one_page_shortfall(900) == 900 - DEFAULT_PER_PAGE
    assert one_page_shortfall(31) == 1
    assert one_page_shortfall(30) == 0
    assert one_page_shortfall(7) == 0
    assert one_page_shortfall(900, 100) == 800


def test_a_count_above_the_ceiling_is_unreachable_at_any_page_size():
    state, detail = verdict("files", 4200, 30, MAX_PER_PAGE)
    assert state == "beyond-cap"
    assert "1200" in detail
    assert "any page size" in detail
    assert verdict("commits", 812, 3, MAX_PER_PAGE)[0] == "beyond-cap"


def test_a_page_count_that_cannot_hold_the_counter_is_its_own_finding():
    state, detail = verdict("files", 150, 1, MAX_PER_PAGE)
    assert state == "counter-disagrees"
    assert "between 1 and 100" in detail


def test_a_reconcilable_multi_page_list_names_what_one_page_misses():
    state, detail = verdict("files", 900, 9, MAX_PER_PAGE)
    assert state == "multi-page"
    assert "misses 870" in detail


def test_a_small_pull_request_is_not_a_finding():
    assert verdict("files", 7, 1, MAX_PER_PAGE)[0] == "single-page"
    assert verdict("commits", 30, 1, MAX_PER_PAGE)[0] == "single-page"


def test_a_missing_counter_is_reported_rather_than_assumed():
    assert verdict("files", None, 1, MAX_PER_PAGE)[0] == "unknown"
    assert verdict("files", "several", 1, MAX_PER_PAGE)[0] == "unknown"
    assert verdict("comments", 12, 1, MAX_PER_PAGE)[0] == "unknown"


def test_an_unknown_page_count_does_not_manufacture_a_disagreement():
    # rel=next with no rel=last: the page count is genuinely unknown, so the
    # only honest verdict is the one the counter alone supports.
    assert verdict("files", 900, None, MAX_PER_PAGE)[0] == "multi-page"


def test_the_two_repairs_are_not_interchangeable():
    assert "vnd.github.diff" in repair("beyond-cap", "files")
    assert "vnd.github.diff" not in repair("beyond-cap", "commits")
    assert "/commits" in repair("beyond-cap", "commits")
    assert "per_page=100" in repair("multi-page", "files")
    assert "changed_files" in repair("counter-disagrees", "files")
    assert repair("single-page", "files").startswith("nothing on this")


def test_the_page_count_is_read_from_the_header_not_guessed():
    header = ('<https://api.github.com/repos/o/n/pulls/1/files?page=2>; rel="next", '
              '<https://api.github.com/repos/o/n/pulls/1/files?page=9>; rel="last"')
    links = parse_link(header)
    assert page_of(links["last"]) == 9
    assert last_page_from(links) == 9
    assert last_page_from({"next": "https://api.github.com/x?page=2"}) is None
    assert last_page_from({}) == 1
    assert page_of(None) is None


def test_the_run_says_what_it_will_spend():
    assert read_cost([1, 2]) == 6
    assert read_cost([4821]) == 3
    assert read_cost([]) == 0
    assert read_cost(None) == 0
