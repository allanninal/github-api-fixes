from github_org_base_permission import (
    base_rank, base_state, count_from_link, coverage_state, drift,
    last_page_from_link, org_total, read_cost, repair, verdict,
)

LINK = ('<https://api.github.com/user/repos?per_page=1&page=2>; rel="next", '
        '<https://api.github.com/user/repos?per_page=1&page=9>; rel="last"')
ORG = {"default_repository_permission": "none",
       "public_repos": 12, "total_private_repos": 400}


def test_the_last_page_number_is_the_count_at_one_per_page():
    assert last_page_from_link(LINK) == 9
    count, how = count_from_link(LINK, 1)
    assert count == 9
    assert 'rel="last"' in how


def test_a_single_page_is_not_a_count_of_zero():
    # The collection fits on one page, so there is no rel="last" at all. Saying
    # zero here would report a working integration as having lost everything.
    count, how = count_from_link(None, 1)
    assert count == 1
    assert "single page" in how
    assert count_from_link("", 0) == (0, 'the first page came back empty and '
                                         'carried no rel="last"')


def test_the_link_parse_survives_a_url_with_commas_in_it():
    header = ('<https://api.github.com/search?q=a,b&per_page=1&page=3>; rel="last"')
    assert last_page_from_link(header) == 3
    assert last_page_from_link('<https://x/?page=notanumber>; rel="last"') is None
    assert last_page_from_link('<https://x/?page=2>; rel="next"') is None


def test_the_base_permission_says_what_it_implies():
    value, detail = base_state(ORG)
    assert value == "none"
    assert "were not added to" in detail
    assert base_state({"default_repository_permission": "read"})[1].startswith(
        "every member can read")
    assert base_rank("none") < base_rank("read") < base_rank("write")


def test_an_absent_base_permission_is_unreadable_not_none():
    value, detail = base_state({"login": "acme"})
    assert value is None
    assert "unreadable rather than absent" in detail
    assert verdict(None, "collapsed")[0] == "base-unreadable"


def test_the_organization_total_adds_both_halves():
    total, detail = org_total(ORG)
    assert total == 412
    assert "public 12" in detail
    assert org_total({"login": "acme"})[0] is None


def test_coverage_is_graded_rather_than_reported_as_a_ratio():
    assert coverage_state(9, 412) == "collapsed"
    assert coverage_state(0, 412) == "collapsed"
    assert coverage_state(150, 412) == "shrunken"
    assert coverage_state(300, 412) == "partial"
    assert coverage_state(412, 412) == "full"
    assert coverage_state(5, None) == "unknown"
    assert coverage_state(0, 0) == "nothing-to-cover"


def test_the_finding_names_the_field_only_when_the_field_fits():
    state, detail = verdict("none", "collapsed")
    assert state == "base-none-implicit-access-gone"
    assert "never granted, only defaulted" in detail


def test_a_collapsed_coverage_under_read_is_somebody_elses_problem():
    # The script has just read the base permission, which makes it the easiest
    # thing in the room to blame. It refuses.
    state, detail = verdict("read", "collapsed")
    assert state == "coverage-lost-elsewhere"
    assert "not this field" in detail
    assert "repository selection" in detail


def test_explicit_grants_are_reported_as_immunity():
    state, detail = verdict("none", "full")
    assert state == "base-none-explicit-grants-hold"
    assert "not exposed to this change" in detail


def test_drift_is_reported_in_both_directions():
    state, detail = drift("read", "none")
    assert state == "base-tightened"
    assert "re-graded every repository at once" in detail
    assert drift("read", "write")[0] == "base-loosened"
    assert drift("read", "read")[0] == "base-unchanged"
    assert drift(None, "read")[0] == "drift-unknown"
    assert drift("read", None)[0] == "drift-unknown"


def test_the_repair_refuses_to_recommend_the_easy_fix():
    fix = repair("base-none-implicit-access-gone", "acme")
    assert "add this account" in fix and "acme" in fix
    assert "Do not raise the base permission back" in fix
    assert "still a member" in repair("coverage-lost-elsewhere", "acme")


def test_the_run_costs_three_reads():
    assert read_cost() == 3
