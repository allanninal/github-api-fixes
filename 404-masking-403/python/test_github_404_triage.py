from github_404_triage import scope_list, token_kind, verdict


def probe(**kw):
    base = {"repo_status": 404, "authenticated": True, "scopes": ["repo"],
            "token_kind": "classic PAT", "in_installation": None}
    base.update(kw)
    return base


def test_prefixes_name_the_credential_without_sending_it():
    assert token_kind("ghp_abc123") == "classic PAT"
    assert token_kind("github_pat_11ABCDE") == "fine-grained PAT"
    assert token_kind("ghs_installation") == "App installation token"
    assert token_kind("  gho_padded  ") == "OAuth user token"
    assert token_kind("v1.0123deadbeef") == "unknown"
    assert token_kind(None) == "unknown"


def test_absent_scopes_header_is_not_an_empty_one():
    # The whole branch between "fine-grained token" and "classic token with
    # nothing ticked" hangs on this distinction.
    assert scope_list(None) is None
    assert scope_list("") == []
    assert scope_list("repo, read:org") == ["repo", "read:org"]


def test_dead_token_beats_every_other_reading():
    state, detail = verdict(probe(authenticated=False, scopes=None))
    assert state == "bad-credentials"
    assert "public" in detail


def test_a_repository_that_answers_is_visible():
    assert verdict(probe(repo_status=200))[0] == "visible"


def test_a_real_403_is_reported_as_the_honest_one():
    state, detail = verdict(probe(repo_status=403))
    assert state == "plain-403"
    assert "rate limit" in detail


def test_classic_token_without_repo_scope_names_the_scope():
    state, detail = verdict(probe(scopes=["public_repo"]))
    assert state == "missing-scope"
    assert "public_repo" in detail


def test_no_scopes_at_all_is_still_a_classic_token():
    state, detail = verdict(probe(scopes=[]))
    assert state == "missing-scope"
    assert "no scopes at all" in detail


def test_missing_scope_header_means_a_fine_grained_token():
    state, _ = verdict(probe(scopes=None, token_kind="fine-grained PAT"))
    assert state == "repository-not-granted"


def test_app_token_outside_the_installation_is_its_own_state():
    state, _ = verdict(probe(token_kind="App installation token",
                             scopes=None, in_installation=False))
    assert state == "not-in-installation"


def test_app_token_inside_the_installation_points_at_metadata():
    state, detail = verdict(probe(token_kind="App installation token",
                                  scopes=None, in_installation=True))
    assert state == "metadata-permission"
    assert "Metadata" in detail


def test_the_indistinguishable_case_stays_indistinguishable():
    # Alive, scoped, still 404. The script must not guess which of the two it is.
    state, detail = verdict(probe())
    assert state == "no-access-or-gone"
    assert "same 404" in detail
