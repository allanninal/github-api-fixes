from github_auth_tier_check import inspect_secret, tier_from_limit, diagnose

GOOD = {"fingerprint": "ghp_ (40 chars)", "kind": "classic personal access token",
        "problems": []}


def test_unset_and_empty_are_not_the_same_finding():
    assert inspect_secret(None)["problems"] == ["unset"]
    assert inspect_secret("")["problems"] == ["empty"]
    assert inspect_secret("   ")["problems"] == ["blank"]


def test_a_normal_token_reports_a_fingerprint_and_no_problems():
    got = inspect_secret("ghp_" + "A" * 36)
    assert got["problems"] == []
    assert got["kind"] == "classic personal access token"
    assert got["fingerprint"] == "ghp_ (40 chars)"


def test_the_fingerprint_never_contains_the_token():
    secret = "ghp_" + "S3CR3T" * 6
    got = inspect_secret(secret)
    assert "S3CR3T" not in got["fingerprint"]
    assert secret not in repr(got)


def test_a_fine_grained_token_is_recognised():
    assert inspect_secret("github_pat_" + "B" * 60)["kind"].startswith("fine-grained")


def test_an_app_installation_token_is_recognised():
    assert "installation" in inspect_secret("ghs_" + "C" * 36)["kind"]


def test_surrounding_quotes_survived_the_paste():
    got = inspect_secret('"ghp_' + "A" * 36 + '"')
    assert "quoted" in got["problems"]
    assert got["kind"] == "classic personal access token"


def test_the_scheme_word_ended_up_in_the_variable():
    got = inspect_secret("Bearer ghp_" + "A" * 36)
    assert "scheme-included" in got["problems"]
    assert got["kind"] == "classic personal access token"
    assert "scheme-included" in inspect_secret("token ghp_x")["problems"]


def test_a_trailing_newline_from_a_file_is_reported():
    assert "padded" in inspect_secret("ghp_" + "A" * 36 + "\n")["problems"]


def test_the_placeholder_from_the_example_file_is_caught():
    got = inspect_secret("your_token_here")
    assert "unknown-prefix" in got["problems"]
    assert "placeholder" in got["problems"]


def test_a_real_token_is_never_accused_of_being_a_placeholder():
    # Placeholder wording is only looked for once the prefix has failed, so a
    # legitimate token containing "xxx" by chance stays clean.
    got = inspect_secret("ghp_xxx" + "A" * 33)
    assert got["problems"] == []


def test_sixty_is_the_only_boundary_that_matters():
    assert tier_from_limit(60)[0] == "anonymous"
    assert tier_from_limit(5000)[0] == "authenticated"
    assert tier_from_limit(15000)[0] == "enterprise"
    assert tier_from_limit(12500)[0] == "scaled"
    assert tier_from_limit(None)[0] == "unknown"


def test_five_thousand_is_reported_as_ambiguous_rather_than_as_a_user():
    _, note = tier_from_limit(5000)
    assert "App installation" in note


def test_a_missing_variable_is_named_as_such():
    state, detail = diagnose(60, 60, 401, inspect_secret(None))
    assert state == "no-token"
    assert "not set" in detail


def test_a_token_that_is_present_but_not_arriving_is_a_different_state():
    state, detail = diagnose(60, 60, 401, GOOD)
    assert state == "anonymous"
    assert "not arriving" in detail


def test_the_quoting_problem_is_named_in_the_anonymous_verdict():
    secret = inspect_secret('"ghp_' + "A" * 36 + '"')
    _, detail = diagnose(60, 60, 401, secret)
    assert "surrounding quotes" in detail


def test_a_rejected_token_is_not_reported_as_a_missing_one():
    state, detail = diagnose(5000, 60, 401, GOOD)
    assert state == "token-rejected"
    assert "expired" in detail


def test_a_403_points_at_sso_rather_than_at_the_tier():
    state, detail = diagnose(5000, 60, 403, GOOD)
    assert state == "blocked"
    assert "SSO" in detail


def test_the_healthy_case_cites_the_control():
    state, detail = diagnose(5000, 60, 200, GOOD)
    assert state == "authenticated"
    assert "control reports 60" in detail
