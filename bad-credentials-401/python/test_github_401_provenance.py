from github_401_provenance import diagnose, from_github, message_of, rung


def gh(status, message=None, login=None, github=True):
    return {"status": status, "message": message, "login": login, "github": github}


def test_the_two_messages_get_different_symbols():
    assert rung(401, "bad credentials") == "rejected"
    assert rung(401, "requires authentication") == "anonymous"


def test_a_401_with_neither_message_is_not_forced_into_one():
    assert rung(401, None) == "unlabelled-401"
    assert rung(401, "something new") == "unlabelled-401"


def test_the_ordinary_statuses_reduce_predictably():
    assert rung(200, None) == "ok"
    assert rung(204, None) == "ok"
    assert rung(403, "forbidden") == "forbidden"
    assert rung(404, None) == "http-404"
    assert rung(0, None) == "error"
    assert rung(None, None) == "error"


def test_the_message_is_only_read_from_a_json_object():
    assert message_of({"message": "  Bad Credentials "}) == "bad credentials"
    assert message_of({"message": ""}) is None
    assert message_of("<html>401</html>") is None
    assert message_of(None) is None


def test_githubs_furniture_is_recognised_whatever_the_header_case():
    assert from_github({"X-GitHub-Request-Id": "ABC:123"}) == (True, "x-github-request-id")
    assert from_github({"Server": "github.com"})[0] is True
    assert from_github({"server": "squid/5.7"}) == (False, None)
    assert from_github({}) == (False, None)


def test_a_401_without_githubs_furniture_is_an_intermediary():
    state, detail = diagnose(gh(401, "bad credentials", github=False),
                             gh(200), gh(401, "bad credentials", github=False))
    assert state == "not-github"
    assert "Re-minting will not help" in detail


def test_a_refused_control_stops_the_diagnosis():
    state, _ = diagnose(gh(401, "bad credentials"), gh(403, "forbidden"), gh(401))
    assert state == "anonymous-refused"


def test_a_control_that_could_not_be_made_is_its_own_state():
    assert diagnose(gh(401, "bad credentials"), gh(0), gh(401))[0] == "no-baseline"


def test_rejected_on_a_public_endpoint_is_the_credential():
    state, detail = diagnose(gh(401, "bad credentials"), gh(200),
                             gh(401, "bad credentials"))
    assert state == "credential-rejected"
    assert "200 without the header and 401 with it" in detail


def test_requires_authentication_means_the_header_never_arrived():
    state, detail = diagnose(gh(200), gh(200), gh(401, "requires authentication"))
    assert state == "header-not-arriving"
    assert "carried nothing" in detail


def test_a_credential_accepted_on_one_path_and_refused_on_another():
    state, _ = diagnose(gh(200), gh(200), gh(401, "bad credentials"))
    assert state == "path-dependent"


def test_a_valid_credential_for_the_wrong_account_is_a_failure():
    state, detail = diagnose(gh(200), gh(200), gh(200, login="someone-else"),
                             expected_login="acme-ci-bot")
    assert state == "wrong-account"
    assert "someone-else" in detail


def test_the_login_comparison_ignores_case():
    assert diagnose(gh(200), gh(200), gh(200, login="Acme-CI-Bot"),
                    expected_login="acme-ci-bot")[0] == "credential-valid"


def test_a_403_on_user_is_not_a_bad_credential():
    state, detail = diagnose(gh(200), gh(200), gh(403, "forbidden"))
    assert state == "authenticated-but-forbidden"
    assert "SSO" in detail


def test_a_working_credential_sends_you_to_look_elsewhere():
    state, detail = diagnose(gh(200), gh(200), gh(200, login="acme-ci-bot"))
    assert state == "credential-valid"
    assert "acme-ci-bot" in detail


def test_probes_that_disagree_are_reported_as_unclear_rather_than_guessed():
    assert diagnose(gh(500), gh(200), gh(500))[0] == "unclear"
