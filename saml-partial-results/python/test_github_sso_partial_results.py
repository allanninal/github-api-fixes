from github_sso_partial_results import parse_sso, verdict


def test_partial_form_yields_the_withheld_ids():
    sso = parse_sso("partial-results; organizations=21955855,20582480")
    assert sso["kind"] == "partial-results"
    assert sso["organizations"] == ["21955855", "20582480"]
    assert sso["url"] is None


def test_required_form_yields_the_authorization_url():
    sso = parse_sso("required; url=https://github.com/orgs/acme/sso?x=1")
    assert sso["kind"] == "required"
    assert sso["url"] == "https://github.com/orgs/acme/sso?x=1"


def test_absent_and_blank_headers_are_the_same_nothing():
    assert parse_sso(None)["kind"] == "none"
    assert parse_sso("")["kind"] == "none"
    assert parse_sso("   ")["kind"] == "none"


def test_an_unrecognised_value_is_never_read_as_absence():
    # The whole point. A header GitHub sent that this parser did not understand
    # must not fall through to "clean".
    sso = parse_sso("some-future-directive; organizations=1")
    assert sso["kind"] == "unknown"
    assert verdict(200, sso, 4)[0] == "unreadable"


def test_a_200_with_partial_results_is_a_failure():
    sso = parse_sso("partial-results; organizations=21955855,20582480")
    state, detail = verdict(200, sso, 4)
    assert state == "partial"
    assert "4 organization(s)" in detail
    assert "2 withheld" in detail
    assert "21955855" in detail


def test_a_403_with_the_required_form_is_the_loud_version():
    sso = parse_sso("required; url=https://github.com/orgs/acme/sso")
    state, detail = verdict(403, sso, 0)
    assert state == "authorization-required"
    assert "https://github.com/orgs/acme/sso" in detail


def test_a_403_without_the_header_is_not_an_sso_problem():
    state, detail = verdict(403, parse_sso(None), 0)
    assert state == "forbidden"
    assert "read:org" in detail


def test_a_clean_200_is_complete():
    state, detail = verdict(200, parse_sso(None), 6)
    assert state == "complete"
    assert "6 organization(s)" in detail


def test_the_header_outranks_the_status_code():
    # A partial-results header on a 200 is worse news than a 403, so it must not
    # be reachable only through the non-200 branch.
    sso = parse_sso("partial-results; organizations=99")
    assert verdict(200, sso, 1)[0] == "partial"
    assert verdict(500, sso, 1)[0] == "partial"
