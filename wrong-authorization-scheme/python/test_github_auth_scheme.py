from github_auth_scheme import (
    check_pairing, credential_kind, explain_401, looks_like_jwt,
    parse_authorization,
)

FAKE_JWT = "eyJhbG.eyJpc3M.sig"


def test_a_jwt_is_recognised_by_shape_without_being_decoded():
    assert looks_like_jwt(FAKE_JWT) is True
    assert looks_like_jwt("eyJhbG.eyJpc3M") is False
    assert looks_like_jwt("eyJhbG..sig") is False
    assert looks_like_jwt("not.a.jwt") is False
    assert looks_like_jwt("") is False


def test_each_prefix_names_its_credential_type():
    assert credential_kind("ghp_fake") == "classic-pat"
    assert credential_kind("gho_fake") == "oauth-user-token"
    assert credential_kind("ghu_fake") == "user-to-server-token"
    assert credential_kind("ghs_fake") == "installation-token"
    assert credential_kind("ghr_fake") == "refresh-token"
    assert credential_kind("github_pat_fk") == "fine-grained-pat"


def test_a_jwt_wins_over_the_prefix_table():
    assert credential_kind(FAKE_JWT) == "app-jwt"


def test_the_unprefixed_legacy_shape_is_still_recognised():
    assert credential_kind("0" * 40) == "legacy-pat"
    assert credential_kind("something-else") == "unknown"
    assert credential_kind(None) == "absent"
    assert credential_kind("   ") == "absent"


def test_a_bare_value_has_no_scheme():
    assert parse_authorization("ghp_fake")["scheme"] is None
    assert parse_authorization("ghp_fake")["has_credential"] is True


def test_a_scheme_and_a_value_are_split_on_whitespace():
    parsed = parse_authorization("Bearer  ghp_fake")
    assert parsed["scheme"] == "Bearer"
    assert parsed["words"] == 2


def test_an_absent_header_is_not_an_empty_one():
    assert parse_authorization(None)["has_credential"] is False
    assert parse_authorization("")["has_credential"] is False


def test_a_jwt_under_the_token_word_is_the_headline_failure():
    state, detail, repair = check_pairing("token", "app-jwt")
    assert state == "jwt-with-token-scheme"
    assert "Bearer" in detail
    assert "Bearer" in repair


def test_a_jwt_under_bearer_is_fine():
    assert check_pairing("Bearer", "app-jwt")[0] == "bearer-ok"


def test_the_scheme_word_is_read_case_insensitively():
    assert check_pairing("bearer", "app-jwt")[0] == "bearer-ok"
    assert check_pairing("TOKEN", "app-jwt")[0] == "jwt-with-token-scheme"


def test_a_pat_under_the_legacy_word_works_and_is_still_reported():
    state, detail, _ = check_pairing("token", "classic-pat")
    assert state == "legacy-scheme-accepted"
    assert "nothing is failing because of it today" in detail


def test_a_bare_value_is_its_own_state():
    assert check_pairing(None, "classic-pat")[0] == "scheme-missing"


def test_basic_is_sent_to_the_other_note():
    assert check_pairing("Basic", "classic-pat")[0] == "basic-scheme"


def test_an_unread_scheme_word_is_named():
    state, detail, _ = check_pairing("OAuth", "classic-pat")
    assert state == "unknown-scheme"
    assert "OAuth" in detail


def test_a_refresh_token_is_not_an_api_credential():
    assert check_pairing("Bearer", "refresh-token")[0] == "refresh-token-sent"


def test_no_credential_is_not_a_scheme_problem():
    assert check_pairing("Bearer", "absent")[0] == "no-credential"


def test_the_specific_messages_beat_the_generic_one():
    assert explain_401("A JSON web token could not be decoded.")[0] == "jwt-expected"
    assert explain_401("Requires authentication")[0] == "nothing-arrived"
    assert explain_401("Bad credentials")[0] == "received-and-refused"


def test_an_unfamiliar_message_is_admitted_rather_than_guessed():
    assert explain_401("Something else entirely")[0] == "unmapped-message"
    assert explain_401(None)[0] == "unmapped-message"
