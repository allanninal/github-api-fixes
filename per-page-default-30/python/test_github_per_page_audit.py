from github_per_page_audit import pages_for, verdict


def test_page_count_is_a_ceiling_not_a_division():
    assert pages_for(3000, 30) == 100
    assert pages_for(3000, 100) == 30
    assert pages_for(3001, 100) == 31
    assert pages_for(1, 100) == 1


def test_per_page_above_the_maximum_is_clamped_to_100():
    # The API reduces it silently rather than rejecting it, so the arithmetic
    # has to reduce it too or the saving reported here is fiction.
    assert pages_for(3000, 500) == 30
    state, detail = verdict(3000, 500)
    assert state == "at-maximum"
    assert "clamped" in detail


def test_the_default_page_size_is_the_finding():
    state, detail = verdict(3412, 30)
    assert state == "wasteful"
    assert "114 request(s) at per_page=30" in detail
    assert "35 at per_page=100" in detail
    assert "79 request(s)" in detail


def test_a_full_page_size_has_nothing_to_recover():
    assert verdict(3412, 100)[0] == "at-maximum"


def test_a_short_list_is_one_request_either_way():
    state, _ = verdict(12, 30)
    assert state == "single-page"


def test_an_empty_collection_is_not_reported_as_wasteful():
    assert verdict(0, 30)[0] == "empty"
    assert pages_for(0, 30) == 0
