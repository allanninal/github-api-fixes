from github_endpoint_audience import (
    accepts, canonical, guess, substitute, verdict,
)


def test_a_concrete_path_reduces_to_its_template():
    assert canonical("/repos/acme/api/issues") == "/repos/{owner}/{repo}/issues"
    assert canonical("/users/octocat") == "/users/{username}"


def test_query_strings_fragments_and_slashes_do_not_change_the_route():
    assert canonical("/user/repos?per_page=100") == "/user/repos"
    assert canonical("/user/repos/") == "/user/repos"
    assert canonical("https://api.github.com/user/repos") == "/user/repos"
    assert canonical("user/repos") == "/user/repos"


def test_user_and_users_are_different_routes():
    assert canonical("/user") == "/user"
    assert canonical("/users/octocat") != "/user"


def test_an_unknown_path_is_not_forced_onto_a_template():
    assert canonical("/enterprises/acme/audit-log") is None


def test_the_route_table_answers_only_for_known_routes():
    assert "s2s" not in accepts("/user")
    assert "s2s" in accepts("/repos/{owner}/{repo}/issues")
    assert accepts("/nowhere") is None


def test_the_heuristic_covers_the_user_family_and_declines_the_rest():
    classes, _ = guess("/user/blocks")
    assert classes == {"any", "u2s"}
    classes, reason = guess("/enterprises/acme/audit-log")
    assert classes is None
    assert "not in the table" in reason


def test_the_heuristic_knows_app_routes_want_the_jwt():
    classes, _ = guess("/app/hook/config")
    assert classes == {"jwt"}


def test_a_dead_credential_is_never_reported_as_a_route_problem():
    state, detail = verdict(False, 403, "/user", {"any", "u2s"})
    assert state == "not-an-installation-token"
    assert "not the mismatch" in detail


def test_a_route_that_wants_a_person_names_that_and_not_a_permission():
    state, detail = verdict(True, 403, "/user", accepts("/user"))
    assert state == "needs-user-context"
    assert "no permission opens it" in detail


def test_a_route_that_wants_the_app_jwt_is_its_own_state():
    state, detail = verdict(True, 401, "/app", accepts("/app"))
    assert state == "needs-app-jwt"
    assert "sign a fresh JWT" in detail


def test_a_route_that_does_accept_installation_tokens_is_sent_elsewhere():
    state, detail = verdict(True, 403, "/repos/{owner}/{repo}/hooks",
                            accepts("/repos/{owner}/{repo}/hooks"))
    assert state == "installation-tokens-accepted"
    assert "x-accepted-github-permissions" in detail


def test_a_successful_call_is_not_a_finding():
    assert verdict(True, 200, "/installation/repositories",
                   accepts("/installation/repositories"))[0] == "endpoint-accepted"


def test_an_unknown_route_says_so_rather_than_guessing():
    state, detail = verdict(True, 403, None, None)
    assert state == "route-unknown"
    assert "genuinely unknown" in detail


def test_a_heuristic_answer_is_labelled_as_one():
    _, detail = verdict(True, 403, None, {"any", "u2s"}, guessed=True)
    assert "by heuristic" in detail


def test_substitutes_exist_where_there_is_an_equivalent_and_not_where_there_is_not():
    assert substitute("/user/repos")[0] == "/installation/repositories"
    assert substitute("/gists")[0] is None
    assert substitute("/repos/{owner}/{repo}") is None
