from github_search_cap_audit import last_reachable_page, reach


def test_a_small_query_is_fully_reachable():
    state, detail = reach(240, 100)
    assert state == "reachable"
    assert "3 request(s)" in detail


def test_a_query_over_the_cap_names_what_is_unreachable():
    state, detail = reach(24831, 100)
    assert state == "capped"
    assert "23831 match(es)" in detail
    assert "at least 25 narrower queries" in detail


def test_just_under_the_cap_is_a_warning_not_a_pass():
    # 950 works today and silently loses results the moment it passes 1,000.
    state, detail = reach(950, 100)
    assert state == "near-cap"
    assert "950" in detail


def test_no_matches_is_not_confused_with_a_capped_query():
    assert reach(0, 100)[0] == "no-matches"
    assert reach(None, 100)[0] == "no-matches"


def test_the_last_working_page_depends_on_the_page_size():
    assert last_reachable_page(100) == 10
    assert last_reachable_page(30) == 33
    assert last_reachable_page(1) == 1000


def test_page_size_above_the_maximum_is_clamped_before_the_arithmetic():
    assert last_reachable_page(500) == 10
