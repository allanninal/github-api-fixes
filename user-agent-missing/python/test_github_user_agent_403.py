from github_user_agent_403 import (
    classify_403, grade_user_agent, suggest_user_agent, verdict,
)


def test_an_absent_header_is_not_an_empty_one():
    assert grade_user_agent(None)[0] == "absent"
    assert grade_user_agent("")[0] == "empty"
    assert grade_user_agent("   ")[0] == "empty"


def test_a_library_default_satisfies_the_rule_and_identifies_nobody():
    assert grade_user_agent("python-requests/2.31.0")[0] == "library-default"
    assert grade_user_agent("Go-http-client/1.1")[0] == "library-default"
    assert grade_user_agent("curl/8.4.0")[0] == "library-default"


def test_a_named_application_with_a_version_and_a_contact_is_descriptive():
    grade, _ = grade_user_agent("acme-repo-auditor/1.2 (+https://acme.example)")
    assert grade == "descriptive"


def test_half_an_identity_is_reported_as_half():
    assert grade_user_agent("acme-repo-auditor/1.2")[0] == "named"
    assert grade_user_agent("acme (+https://acme.example)")[0] == "named"
    assert grade_user_agent("auditor")[0] == "opaque"


def test_the_user_agent_rule_names_itself_in_the_body():
    state, detail = classify_403(
        "Request forbidden by administrative rules. Please make sure your "
        "request has a User-Agent header.", {})
    assert state == "user-agent-rule"
    assert "User-Agent" in detail


def test_quota_exhaustion_is_read_from_a_header_not_from_words():
    state, _ = classify_403("API rate limit exceeded",
                            {"X-RateLimit-Remaining": "0"})
    assert state == "primary-rate-limit"


def test_a_secondary_limit_is_not_confused_with_the_primary_one():
    state, _ = classify_403("You have exceeded a secondary rate limit",
                            {"x-ratelimit-remaining": "4998"})
    assert state == "secondary-rate-limit"


def test_a_permission_refusal_is_sorted_away_from_this_page():
    state, _ = classify_403("Resource not accessible by integration", {})
    assert state == "permission"


def test_an_unfamiliar_403_is_admitted_rather_than_guessed():
    assert classify_403("Something new", {})[0] == "unclassified-403"


def test_the_missing_header_verdict_says_what_was_actually_sent():
    state, detail = verdict(
        403, "Request forbidden by administrative rules. Please make sure "
             "your request has a User-Agent header.", {}, None)
    assert state == "user-agent-missing"
    assert "nothing" in detail


def test_a_quota_403_is_not_reported_as_a_header_problem():
    state, detail = verdict(403, "API rate limit exceeded",
                            {"x-ratelimit-remaining": "0"}, "acme/1.0 (+http://a)")
    assert state == "primary-rate-limit"
    assert "no User-Agent will repair it" in detail


def test_a_401_is_sent_to_the_credential_notes():
    assert verdict(401, "Bad credentials", {}, "acme/1.0")[0] == "not-a-user-agent-problem"


def test_a_successful_request_with_a_default_agent_is_still_a_finding():
    state, _ = verdict(200, None, {}, "python-requests/2.31.0")
    assert state == "identifiable-agent-missing"


def test_a_successful_request_with_a_descriptive_agent_passes():
    state, _ = verdict(200, None, {}, "acme-auditor/1.2 (+https://acme.example)")
    assert state == "user-agent-ok"


def test_the_suggested_header_always_grades_as_descriptive():
    agent = suggest_user_agent("Acme Repo Auditor!", "1.2",
                               "https://acme.example/contact")
    assert agent == "acme-repo-auditor/1.2 (+https://acme.example/contact)"
    assert grade_user_agent(agent)[0] == "descriptive"


def test_an_unnameable_application_still_produces_a_usable_header():
    assert suggest_user_agent("!!!").startswith("unnamed-integration/")
