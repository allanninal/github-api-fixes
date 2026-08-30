from github_fine_grained_pat_probe import (
    actor_from_message, classify, grant_from_probe, graphql_pat_refusals,
    identify, missing_permissions, operations, parse_accepted_permissions,
    refusal, repair, scope_header_state, token_kind, token_prefix,
    where_the_requirement_lives,
)

# Obviously fake, and short enough that nobody could mistake one for a secret.
FG = "github_pat_FAKE"
CLASSIC = "ghp_FAKE"
APP = "ghs_FAKE"

REFUSED = {"x-accepted-github-permissions": "issues=read"}
PAT_403 = "Resource not accessible by personal access token"
APP_403 = "Resource not accessible by integration"


def test_a_fine_grained_token_is_known_by_a_header_that_is_not_there():
    kind, detail = identify(FG, {"x-github-api-version-selected": "2022-11-28"})
    assert kind == "fine-grained personal access token"
    assert "no x-oauth-scopes header" in detail
    assert token_prefix(FG) == "github_pat_"


def test_an_empty_scope_header_is_the_opposite_of_a_missing_one():
    assert scope_header_state({"x-oauth-scopes": ""}) == "present-empty"
    assert scope_header_state({"X-OAuth-Scopes": "repo"}) == "present"
    assert scope_header_state({"x-github-request-id": "abc"}) == "absent"
    assert scope_header_state(None) == "absent"
    # A classic token with no scopes still sends the header, so present-empty
    # identifies a classic token just as firmly as a populated one does.
    kind, _ = identify(CLASSIC, {"x-oauth-scopes": ""})
    assert kind == "classic personal access token"


def test_every_documented_prefix_is_named():
    assert token_kind(FG).startswith("fine-grained")
    assert token_kind(CLASSIC).startswith("classic")
    assert token_kind(APP) == "GitHub App installation token"
    assert token_kind("nonsense") == "unrecognised credential"
    assert token_prefix("nonsense") == "none"


def test_the_message_names_the_actor_and_routes_the_repair():
    assert actor_from_message(PAT_403) == "fine-grained-pat"
    assert actor_from_message(APP_403) == "github-app"
    assert actor_from_message("Although you appear to have the correct "
                              "authorization credentials, the OAuth App is "
                              "restricted") == "oauth-app"
    assert actor_from_message("Not Found") is None


def test_an_app_refusal_is_handed_to_the_app_note():
    state, _ = classify(403, APP_403, {}, APP)
    assert state == "not-this-note-app"
    assert "app-permission-missing" in repair(state)


def test_a_classic_token_is_handed_to_the_scope_note():
    state, _ = classify(403, "Must have admin rights to Repository.",
                        {"x-oauth-scopes": "public_repo"}, CLASSIC)
    assert state == "not-this-note-classic"
    assert "missing-oauth-scope" in repair(state)


def test_the_fine_grained_refusal_names_what_the_endpoint_accepts():
    state, detail = classify(403, PAT_403, REFUSED, FG)
    assert state == "fine-grained-permission-missing"
    assert "issues=read" in detail
    assert "issues=read" in repair(state, REFUSED)


def test_an_organization_only_refusal_is_not_a_missing_permission():
    state, detail = classify(403, PAT_403, REFUSED, FG, org_only=True)
    assert state == "org-resource-refused"
    assert "approval" in detail
    assert "approve this token" in repair(state)


def test_commas_are_alternatives_and_semicolons_are_requirements():
    assert parse_accepted_permissions("issues=read") == [[("issues", "read")]]
    assert parse_accepted_permissions("issues=read,pull_requests=read") == [
        [("issues", "read")], [("pull_requests", "read")]]
    assert parse_accepted_permissions("contents=read;pull_requests=write") == [
        [("contents", "read"), ("pull_requests", "write")]]
    assert parse_accepted_permissions("metadata") == [[("metadata", "read")]]
    assert parse_accepted_permissions("") == []


def test_a_probe_has_three_outcomes_because_a_404_is_not_a_no():
    assert grant_from_probe(200, "")[0] == "granted"
    assert grant_from_probe(403, PAT_403)[0] == "refused"
    assert grant_from_probe(403, APP_403)[0] == "refused-other"
    assert grant_from_probe(401, "Bad credentials")[0] == "unauthenticated"
    verdict, why = grant_from_probe(404, "Not Found")
    assert verdict == "ambiguous"
    assert "404-masking-403" in why
    assert grant_from_probe(None, "")[0] == "error"


def test_a_404_in_the_matrix_is_never_reported_as_a_refusal():
    state, _ = classify(404, "Not Found", {}, FG)
    assert state == "ambiguous-404"
    assert "404-masking-403" in repair(state)


def test_the_missing_permission_is_the_named_one_the_probes_refused():
    grants = {"metadata": "granted", "issues": "refused"}
    assert missing_permissions(REFUSED, grants) == [("issues", "read")]
    assert missing_permissions(REFUSED, {"issues": "granted"}) == []
    assert missing_permissions({}, grants) == []


def test_the_same_refusal_through_graphql_carries_no_header():
    body = {"data": {"repository": None},
            "errors": [{"type": "FORBIDDEN", "path": ["repository", "issues"],
                        "message": PAT_403},
                       {"type": "NOT_FOUND", "message": "Could not resolve"}]}
    found = graphql_pat_refusals(body)
    assert found == [("repository.issues", PAT_403)]
    assert graphql_pat_refusals({"data": {}}) == []
    assert "no x-accepted-github-permissions header" in where_the_requirement_lives("graphql")
    assert "x-accepted-github-permissions header" in where_the_requirement_lives("rest")


def test_the_document_this_script_sends_is_a_read():
    assert operations("query Q { repository(owner: \"a\", name: \"b\") { issues(first: 1) { totalCount } } }") == ["query"]
    assert refusal("mutation M { addStar(input: {}) { clientMutationId } }")
    assert refusal("subscription S { thing { id } }")
    assert refusal("") == "the document contains no operation to send."
