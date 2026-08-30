from github_user_token_liveness import (
    authorize_url, collect_tokens, population_verdict, retry_disposition,
    token_result,
)

ENV = {
    "GH_USER_TOKEN_BEN": "gho_fake2",
    "GH_USER_TOKEN_ALICE": "gho_fake1",
    "GITHUB_TOKEN": "ghp_fake",
    "GH_USER_TOKEN_EMPTY": "",
}


def test_tokens_are_collected_by_prefix_and_sorted():
    found = collect_tokens(ENV, "GH_USER_TOKEN_")
    assert [name for name, _ in found] == ["GH_USER_TOKEN_ALICE",
                                           "GH_USER_TOKEN_BEN"]


def test_an_unrelated_variable_is_not_collected():
    assert all(name != "GITHUB_TOKEN" for name, _ in
               collect_tokens(ENV, "GH_USER_TOKEN_"))


def test_an_empty_value_is_not_a_stored_token():
    assert all(name != "GH_USER_TOKEN_EMPTY" for name, _ in
               collect_tokens(ENV, "GH_USER_TOKEN_"))


def test_a_403_is_not_a_revocation():
    assert token_result(200) == "alive"
    assert token_result(401) == "rejected"
    assert token_result(403) == "forbidden"
    assert token_result(500) == "error"


def test_one_refusal_among_successes_is_that_person():
    state, detail = population_verdict([("a", "alive"), ("b", "rejected"),
                                        ("c", "alive")])
    assert state == "individual-revocation"
    assert "1 of 3" in detail
    assert "b" in detail


def test_every_token_refused_at_once_is_the_application():
    state, detail = population_verdict([("a", "rejected"), ("b", "rejected"),
                                        ("c", "rejected")])
    assert state == "application-wide"
    assert "do not coordinate" in detail


def test_one_stored_token_cannot_separate_the_two_causes():
    state, detail = population_verdict([("a", "rejected")])
    assert state == "single-token-inconclusive"
    assert "cannot be separated" in detail


def test_a_healthy_fleet_says_look_elsewhere():
    assert population_verdict([("a", "alive"), ("b", "alive")])[0] == "all-healthy"


def test_an_empty_fleet_is_not_a_verdict_about_users():
    assert population_verdict([])[0] == "no-tokens"


def test_errors_alone_are_not_read_as_an_application_failure():
    assert population_verdict([("a", "error"), ("b", "error")])[0] == "all-healthy"


def test_a_revoked_token_is_terminal_rather_than_retryable():
    disposition, detail = retry_disposition("rejected")
    assert disposition == "terminal"
    assert "never recovers" in detail


def test_a_failed_probe_is_the_only_retryable_state():
    assert retry_disposition("error")[0] == "retryable"
    assert retry_disposition("forbidden")[0] == "terminal"
    assert retry_disposition("alive")[0] == "none"


def test_the_authorize_url_carries_the_client_id_and_the_scopes():
    url = authorize_url("Iv1.example", ["repo", "read:org"])
    assert url.startswith("https://github.com/login/oauth/authorize?")
    assert "client_id=Iv1.example" in url
    assert "scope=repo+read%3Aorg" in url


def test_optional_parameters_are_omitted_rather_than_sent_empty():
    url = authorize_url("Iv1.example")
    assert "scope=" not in url
    assert "redirect_uri=" not in url
    assert "state=" not in url


def test_a_redirect_and_a_state_are_encoded():
    url = authorize_url("Iv1.example", ["repo"], "https://app.example/cb", "xyz")
    assert "redirect_uri=https%3A%2F%2Fapp.example%2Fcb" in url
    assert "state=xyz" in url
