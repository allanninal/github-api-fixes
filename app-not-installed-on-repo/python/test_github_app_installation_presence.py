from github_app_installation_presence import (
    account_route, classify, creation_order, parse_iso, repair_for, split_repo,
    visibility,
)

JAN = parse_iso("2026-01-01T00:00:00Z")
MAR = parse_iso("2026-03-02T00:00:00Z")


def test_every_shape_of_repository_reference_lands_on_the_same_pair():
    assert split_repo("acme/reporting") == ("acme", "reporting")
    assert split_repo("https://github.com/acme/reporting") == ("acme", "reporting")
    assert split_repo("https://github.com/acme/reporting/") == ("acme", "reporting")
    assert split_repo("https://github.com/acme/reporting/pulls/12") == ("acme", "reporting")
    assert split_repo("https://api.github.com/repos/acme/reporting") == ("acme", "reporting")
    assert split_repo("git@github.com:acme/reporting.git") == ("acme", "reporting")


def test_something_that_is_not_a_repository_reference_is_refused():
    assert split_repo("acme") is None
    assert split_repo("") is None
    assert split_repo(None) is None
    assert split_repo("acme/repo with spaces") is None


def test_organizations_and_user_accounts_have_different_routes():
    assert account_route("acme", "Organization") == "/orgs/acme/installation"
    assert account_route("octocat", "User") == "/users/octocat/installation"
    assert account_route("acme", None) == "/orgs/acme/installation"


def test_a_public_repository_narrows_the_search_to_your_own_app():
    state, detail = visibility(200)
    assert state == "public-repo"
    assert "your side of the request" in detail


def test_a_404_without_a_credential_is_two_answers_and_says_so():
    state, detail = visibility(404)
    assert state == "not-public-or-absent"
    assert "cannot separate those two" in detail
    assert visibility(500)[0] == "visibility-unknown"


def test_installed_on_the_account_but_not_the_repository_is_the_headline():
    state, detail = classify(404, 200)
    assert state == "installed-on-account-not-repo"
    assert "never selected" in detail
    assert "add this repository" in repair_for(state, "selected")


def test_not_installed_at_all_is_a_different_repair_and_a_different_person():
    state, detail = classify(404, 404)
    assert state == "not-installed-on-account"
    assert "admin rights" in repair_for(state, None)


def test_installed_here_means_the_404_came_from_somewhere_else():
    state, detail = classify(200, 200)
    assert state == "installed-on-this-repo"
    assert "about something else" in detail


def test_a_refused_jwt_is_not_reported_as_an_absent_installation():
    assert classify(401, 404)[0] == "jwt-not-accepted"
    assert classify(404, 403)[0] == "jwt-not-accepted"
    assert "Fix the JWT first" in classify(401, 404)[1]


def test_an_unrecognised_pair_gets_no_verdict():
    assert classify(500, 200)[0] == "inconclusive"


def test_a_repository_newer_than_the_installation_is_a_recurring_cause():
    state, detail = creation_order(MAR, JAN, "selected")
    assert state == "repo-created-after-installation"
    assert "60 day(s) after" in detail
    assert "Every repository created from now on" in detail


def test_a_repository_older_than_the_installation_was_left_out_by_hand():
    assert creation_order(JAN, MAR, "selected")[0] == "repo-predates-installation"


def test_an_installation_covering_everything_makes_the_dates_irrelevant():
    state, detail = creation_order(MAR, JAN, "all")
    assert state == "selection-covers-everything"
    assert "automatically" in detail


def test_missing_inputs_produce_a_named_state_rather_than_a_guess():
    assert creation_order(None, JAN, "selected")[0] == "creation-order-unknown"
    assert creation_order(MAR, None, "selected")[0] == "creation-order-unknown"
    assert creation_order(MAR, JAN, None)[0] == "selection-unknown"


def test_timestamps_that_cannot_be_read_are_none_rather_than_an_exception():
    assert parse_iso("2026-01-01T00:00:00Z") is not None
    assert parse_iso("last thursday") is None
    assert parse_iso("") is None
    assert parse_iso(None) is None
