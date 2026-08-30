from github_scope_diff import alternatives, expand, parse_scopes, satisfies, verdict


def test_an_absent_header_is_not_an_empty_scope_list():
    assert parse_scopes(None) is None
    assert parse_scopes("") == []
    assert parse_scopes("repo, read:org") == ["repo", "read:org"]


def test_holding_repo_already_holds_the_narrower_repo_scopes():
    have = expand(["repo"])
    assert "public_repo" in have
    assert "repo:status" in have
    assert "security_events" in have


def test_implication_is_transitive():
    have = expand(["admin:org"])
    assert "write:org" in have
    assert "read:org" in have


def test_expanding_nothing_is_empty_rather_than_an_error():
    assert expand(None) == set()
    assert expand([]) == set()


def test_the_accepted_header_is_parsed_as_alternatives():
    assert alternatives("admin:repo_hook, write:repo_hook") == [
        ("admin:repo_hook",), ("write:repo_hook",)]


def test_an_absent_accepted_header_is_not_an_empty_one():
    assert alternatives(None) is None
    assert alternatives("") == []


def test_a_token_holding_repo_does_not_need_public_repo_added():
    ok, options = satisfies(["repo"], alternatives("public_repo"))
    assert ok is True
    assert options == []


def test_the_narrowest_workable_alternative_wins():
    ok, options = satisfies([], alternatives("repo, public_repo"))
    assert ok is False
    assert options[0] == ("public_repo",)


def test_an_empty_accepted_list_is_satisfied_by_any_token():
    assert satisfies([], []) == (True, [])


def test_an_absent_accepted_list_cannot_be_judged():
    assert satisfies(["repo"], None) == (None, [])


def test_a_missing_scope_is_named_and_the_alternatives_counted():
    state, detail = verdict(403, ["public_repo", "read:org"],
                            alternatives("admin:repo_hook, write:repo_hook"))
    assert state == "missing-scope"
    assert "write:repo_hook" in detail
    assert "2 alternative(s)" in detail


def test_a_fine_grained_credential_is_sent_to_the_other_note():
    state, detail = verdict(403, None, alternatives("repo"))
    assert state == "not-a-scoped-credential"
    assert "x-accepted-github-permissions" in detail


def test_an_empty_accepted_header_rules_scope_out_entirely():
    state, detail = verdict(404, ["repo"], [])
    assert state == "any-token-accepted"
    assert "no scope will fix it" in detail


def test_an_absent_accepted_header_is_its_own_state():
    assert verdict(404, ["repo"], None)[0] == "endpoint-named-no-scopes"


def test_a_satisfied_token_that_still_failed_points_elsewhere():
    state, detail = verdict(404, ["repo"], alternatives("repo"))
    assert state == "scope-satisfied"
    assert "another cause" in detail


def test_a_successful_call_has_nothing_to_diff():
    assert verdict(200, ["repo"], alternatives("repo"))[0] == "call-succeeded"
