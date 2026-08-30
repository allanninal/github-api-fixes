from github_link_header_audit import page_number, parse_link, verdict

FULL = ('<https://api.github.com/repositories/1/pulls?per_page=1&page=2>; rel="next", '
        '<https://api.github.com/repositories/1/pulls?per_page=1&page=340>; rel="last"')


def test_link_header_parses_both_relations():
    links = parse_link(FULL)
    assert set(links) == {"next", "last"}
    assert page_number(links["last"]) == 340


def test_a_comma_inside_a_url_does_not_become_a_second_link():
    # labels=bug,ci is ordinary. Splitting the header on "," makes four broken
    # entries out of two good ones and the walk then terminates on page one.
    header = ('<https://api.github.com/repos/o/n/issues?labels=bug,ci&page=2>; rel="next", '
              '<https://api.github.com/repos/o/n/issues?labels=bug,ci&page=9>; rel="last"')
    links = parse_link(header)
    assert set(links) == {"next", "last"}
    assert links["next"].endswith("labels=bug,ci&page=2")


def test_no_link_header_is_a_single_page():
    state, detail = verdict(parse_link(None), 7, 1)
    assert state == "single-page"
    assert "7 item(s)" in detail


def test_rel_last_at_per_page_one_is_the_exact_count():
    state, detail = verdict(parse_link(FULL), 1, 1)
    assert state == "more-pages"
    assert "340 item(s)" in detail


def test_next_without_last_is_its_own_state():
    header = '<https://api.github.com/repos/o/n/branches?page=2>; rel="next"'
    state, detail = verdict(parse_link(header), 1, 1)
    assert state == "more-pages-unsized"
    assert 'rel="last"' in detail


def test_page_number_is_none_when_there_is_no_page_parameter():
    assert page_number("https://api.github.com/repos/o/n/pulls?per_page=100") is None
    assert page_number(None) is None
