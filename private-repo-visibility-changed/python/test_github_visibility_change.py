from github_visibility_change import (
    ANONYMOUS_CORE_LIMIT, BLIND_SCOPE, PRIVATE_SCOPE, blind_spot, classify,
    client_is_anonymous, fork_fallout, read_cost, repair, scope_gap, scope_list,
    visibility_of,
)

PRIVATE_NOW = {"private": True, "visibility": "private", "forks_count": 37}
INTERNAL = {"private": True, "visibility": "internal", "forks_count": 0}
PUBLIC = {"private": False, "visibility": "public", "forks_count": 12}


def test_the_pair_of_readings_is_the_finding():
    state, detail = classify(404, 200, PRIVATE_NOW)
    assert state == "went-private"
    assert "invisible without one" in detail
    assert "Deletion would answer 404 to both" in detail


def test_404_to_both_readings_is_handed_to_the_wider_triage():
    state, detail = classify(404, 404, None)
    assert state == "invisible-to-both"
    assert "wider 404 triage" in detail
    assert "wider 404 triage" in repair(state)


def test_a_public_repository_is_not_reported_as_a_transition():
    assert classify(200, 200, PUBLIC)[0] == "still-public"


def test_an_anonymous_success_beside_an_authenticated_failure_blames_the_token():
    state, detail = classify(200, 401, None)
    assert state == "token-is-the-problem"
    assert "expired or revoked" in detail


def test_a_redirect_anywhere_is_a_rename_and_a_different_note():
    assert classify(301, 200, PRIVATE_NOW)[0] == "moved"
    assert classify(404, 301, None)[0] == "moved"


def test_internal_is_private_true_and_still_not_private():
    assert visibility_of(INTERNAL) == "internal"
    assert INTERNAL["private"] is True
    state, detail = classify(404, 200, INTERNAL)
    assert state == "internal-visibility"
    assert "every member of the enterprise" in detail
    assert "membership" in repair(state)


def test_visibility_falls_back_to_the_boolean_but_prefers_the_field():
    assert visibility_of({"private": True}) == "private"
    assert visibility_of({"private": False}) == "public"
    assert visibility_of({}) == "unreported"
    assert visibility_of(None) == "unreported"


def test_the_anonymous_bucket_proves_whether_a_client_authenticated():
    assert ANONYMOUS_CORE_LIMIT == 60
    assert client_is_anonymous(60) is True
    assert client_is_anonymous(5000) is False
    assert client_is_anonymous(None) is None


def test_public_repo_is_blind_rather_than_merely_narrow():
    state, detail = scope_gap([BLIND_SCOPE], "private")
    assert state == "blind-scope"
    assert "as blind here as sending no token at all" in detail
    assert BLIND_SCOPE in repair("went-private", state)


def test_the_repo_scope_covers_it_and_points_at_the_account_instead():
    state, detail = scope_gap([PRIVATE_SCOPE, "workflow"], "private")
    assert state == "scope-sufficient"
    assert "no grant on the repository" in detail


def test_a_fine_grained_token_reports_no_scopes_and_needs_permissions():
    state, detail = scope_gap(None, "private")
    assert state == "no-scopes-reported"
    assert "Metadata: Read" in detail and "Contents: Read" in detail


def test_scopes_are_not_asked_about_a_public_repository():
    assert scope_gap(["public_repo"], "public")[0] == "not-applicable"
    assert scope_gap([], "private")[0] == "scope-insufficient"


def test_absent_and_empty_scope_headers_are_different_readings():
    assert scope_list(None) is None
    assert scope_list("") == []
    assert scope_list("repo, workflow") == ["repo", "workflow"]


def test_the_detached_forks_are_reported_as_a_second_failure():
    note = fork_fallout(PRIVATE_NOW)
    assert note and "still public" in note
    assert fork_fallout(PUBLIC) is None
    assert fork_fallout(INTERNAL) is None


def test_the_missing_timestamp_is_stated_rather_than_guessed():
    assert "no visibility-changed timestamp" in blind_spot()
    assert "your own logs" in blind_spot()


def test_an_unsorted_pair_is_left_unsorted():
    state, detail = classify(403, 500, None)
    assert state == "unclassified"
    assert "500" in detail and "403" in detail


def test_the_run_costs_two_billable_reads():
    assert read_cost() == 2
