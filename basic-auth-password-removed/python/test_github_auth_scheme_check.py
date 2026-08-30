import base64

from github_auth_scheme_check import (
    classify, parse_auth_header, password_removed, replacement_header,
    scan_snippet, verdict)

FAKE_TOKEN = "ghp_FAKE0000000001"
FAKE_PASSWORD = "hunter2"


def basic(user, secret):
    return "Basic " + base64.b64encode(
        ("%s:%s" % (user, secret)).encode()).decode()


def test_a_password_and_a_token_in_the_same_shape_classify_differently():
    assert classify(parse_auth_header(basic("octocat", FAKE_PASSWORD))) == "password-basic"
    assert classify(parse_auth_header(basic("octocat", FAKE_TOKEN))) == "token-basic"


def test_a_forty_character_hex_secret_is_read_as_a_legacy_token():
    assert classify(parse_auth_header(basic("octocat", "a" * 40))) == "token-basic"


def test_the_parser_never_returns_the_secret():
    parsed = parse_auth_header(basic("octocat", FAKE_PASSWORD))
    assert FAKE_PASSWORD not in str(parsed)
    assert parsed["secret_length"] == len(FAKE_PASSWORD)
    assert parsed["username_present"] is True


def test_bearer_and_token_schemes_are_recognised():
    assert classify(parse_auth_header("Bearer " + FAKE_TOKEN)) == "bearer"
    assert classify(parse_auth_header("token " + FAKE_TOKEN)) == "token-scheme"


def test_the_scheme_is_matched_case_insensitively():
    assert classify(parse_auth_header("BEARER " + FAKE_TOKEN)) == "bearer"


def test_an_absent_header_is_no_credential_rather_than_an_error():
    assert classify(parse_auth_header(None)) == "no-credential"
    assert classify(parse_auth_header("   ")) == "no-credential"


def test_a_broken_base64_payload_is_not_reported_as_a_password():
    assert classify(parse_auth_header("Basic not-base64!!")) == "undecodable-basic"


def test_an_unfamiliar_scheme_is_named_as_such():
    assert classify(parse_auth_header("Negotiate abcdef")) == "unknown-scheme"


def test_the_retired_mechanism_message_is_recognised_in_a_body():
    assert password_removed({"message": "Support for password authentication "
                                        "was removed. Please use a personal "
                                        "access token instead."}) is True
    assert password_removed({"message": "Bad credentials"}) is False


def test_the_message_match_survives_odd_whitespace():
    assert password_removed({"message": "support   for password\nauthentication "
                                        "was removed"}) is True


def test_a_password_header_is_never_sent():
    state, detail = verdict("password-basic", None, None)
    assert state == "password-basic"
    assert "Nothing was sent" in detail


def test_a_username_and_token_is_flagged_even_though_it_works():
    state, detail = verdict("token-basic", 200, {"login": "octo-bot"})
    assert state == "token-basic"
    assert "on the way out" in detail


def test_a_correct_scheme_with_a_bad_token_is_a_different_problem():
    state, detail = verdict("bearer", 401, {"message": "Bad credentials"})
    assert state == "credential-rejected"
    assert "different problem" in detail


def test_the_retired_message_under_a_bearer_header_means_something_rewrites_it():
    state, _ = verdict("bearer", 401, {"message": "Support for password "
                                                  "authentication was removed."})
    assert state == "password-removed-message"


def test_a_working_bearer_header_is_the_pass():
    assert verdict("bearer", 200, {"login": "octo-bot"})[0] == "ok"


def test_call_sites_are_found_by_shape_and_never_quoted():
    text = "\n".join([
        "curl -u octocat:%s https://api.github.com/user" % FAKE_PASSWORD,
        "Invoke-WebRequest -Uri $u -Credential $c",
        "client = Client(username=u, password=p)",
        "curl -H \"Authorization: Bearer $T\" https://api.github.com/user",
    ])
    sites = scan_snippet(text)
    assert {s["line"] for s in sites} == {1, 2, 3}
    assert FAKE_PASSWORD not in str(sites)


def test_the_replacement_is_a_header_rather_than_a_credential():
    assert replacement_header() == "Authorization: Bearer $GITHUB_TOKEN"
