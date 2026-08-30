from github_scope_blast_radius import (
    blast_radius, capabilities, excess, held_scopes, required, verdict)

USER = {"login": "octo-bot", "public_repos": 12, "total_private_repos": 88}


def test_an_absent_scope_header_means_a_fine_grained_credential():
    scopes, kind = held_scopes({"X-RateLimit-Limit": "5000"})
    assert scopes is None
    assert kind == "not-scope-based"


def test_the_header_is_read_case_insensitively():
    scopes, kind = held_scopes({"X-OAuth-Scopes": "repo, delete_repo"})
    assert scopes == ["repo", "delete_repo"]
    assert kind == "scope-based"


def test_a_token_minted_with_no_scopes_is_not_the_same_as_no_header():
    scopes, kind = held_scopes({"x-oauth-scopes": ""})
    assert scopes == []
    assert kind == "scope-based"


def test_reading_public_data_requires_no_scope_at_all():
    assert required(["public-repos"])["classic"] == []
    assert required(["public-repos"])["fine_grained"] == ["Metadata: Read"]


def test_a_private_read_needs_the_broadest_classic_scope_there_is():
    assert required(["pull-requests"])["classic"] == ["repo"]


def test_an_unrecognised_read_is_reported_rather_than_dropped():
    out = required(["pull-requests", "telemetry"])
    assert out["unknown"] == ["telemetry"]
    assert out["classic"] == ["repo"]


def test_capabilities_are_verbs_and_deduplicated():
    verbs = capabilities(["repo", "public_repo", "delete_repo"])
    assert any("permanently remove" in v for v in verbs)
    assert len(verbs) == len(set(verbs))


def test_read_only_scopes_authorize_no_verbs():
    assert capabilities(["read:org", "read:packages"]) == []


def test_excess_is_everything_outside_the_minimum():
    assert excess(["repo", "delete_repo", "read:org"], ["repo"]) == [
        "delete_repo", "read:org"]


def test_blast_radius_counts_public_and_private_together():
    radius = blast_radius(USER, ["repo"])
    assert radius["repositories"] == 100
    assert radius["write_scopes"] == ["repo"]


def test_a_body_without_counts_reports_no_number_rather_than_zero():
    assert blast_radius({}, ["repo"])["repositories"] is None


def test_a_fine_grained_token_is_the_pass_condition():
    state, detail = verdict("not-scope-based", None, required([]),
                            blast_radius(USER, None))
    assert state == "not-scope-based"
    assert "nothing to narrow" in detail


def test_a_read_only_job_holding_delete_repo_is_flagged():
    held = ["repo", "delete_repo", "workflow"]
    state, detail = verdict("scope-based", held, required(["pull-requests"]),
                            blast_radius(USER, held))
    assert state == "over-scoped"
    assert "delete_repo" in detail
    assert "100 repositories" in detail


def test_unused_read_scopes_are_untidy_rather_than_dangerous():
    held = ["repo", "read:packages"]
    state, detail = verdict("scope-based", held, required(["pull-requests"]),
                            blast_radius(USER, held))
    assert state == "unused-scopes"
    assert "untidy" in detail


def test_the_minimum_classic_scope_is_still_reported_as_too_broad():
    held = ["repo"]
    state, detail = verdict("scope-based", held, required(["pull-requests"]),
                            blast_radius(USER, held))
    assert state == "coarse-by-construction"
    assert "different credential type" in detail


def test_a_genuinely_minimal_token_is_clean():
    held = ["read:org"]
    state, _ = verdict("scope-based", held, required(["org-members"]),
                       blast_radius(USER, held))
    assert state == "least-privilege"
