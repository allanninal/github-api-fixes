from github_token_reach import (
    parse_grant, parse_needs, permission_shortfall, rank, repair, repo_gap,
    verdict,
)

MINT = {"expires_at": "2026-08-30T13:00:00Z",
        "permissions": {"contents": "read", "metadata": "read"},
        "repository_selection": "selected",
        "repositories": [{"full_name": "acme/api"}]}


def test_a_bare_permission_name_means_read():
    assert parse_needs("contents, issues:write") == {"contents": "read",
                                                     "issues": "write"}
    assert parse_needs("") == {}
    assert parse_needs("  ,  ") == {}


def test_levels_are_ranked_rather_than_compared_as_strings():
    assert rank("write") > rank("read") > rank(None)
    assert rank("nonsense") == 0


def test_a_mint_response_is_read_for_the_three_fields_that_matter():
    grant = parse_grant(MINT)
    assert grant["permissions"] == {"contents": "read", "metadata": "read"}
    assert grant["repository_selection"] == "selected"
    assert grant["repositories"] == ["acme/api"]


def test_a_mint_response_with_no_permissions_block_is_unseen_not_empty():
    assert parse_grant({"repository_selection": "all"})["permissions"] is None
    assert parse_grant(None)["permissions"] is None


def test_repository_names_match_regardless_of_case():
    assert repo_gap(["acme/API"], ["Acme/api"]) == []
    assert repo_gap(["acme/api"], ["acme/api", "acme/docs"]) == ["acme/docs"]


def test_an_unseen_grant_is_not_a_permission_pass():
    assert permission_shortfall(None, {"issues": "write"}) is None
    assert permission_shortfall({}, {"issues": "write"}) == [("issues", "write", "absent")]


def test_read_where_write_is_needed_is_a_shortfall():
    assert permission_shortfall({"issues": "read"}, {"issues": "write"}) == \
        [("issues", "write", "read")]
    assert permission_shortfall({"issues": "write"}, {"issues": "read"}) == []


def test_an_unreachable_repository_is_reported_before_a_permission_shortfall():
    state, detail = verdict(True, ["acme/docs"], [("issues", "write", "read")],
                            "selected")
    assert state == "repos-out-of-reach"
    assert "acme/docs" in detail


def test_a_permission_shortfall_names_both_levels():
    state, detail = verdict(True, [], [("issues", "write", "read")], "all")
    assert state == "permissions-below-need"
    assert "issues is read, the job needs write" in detail


def test_an_unseen_grant_gets_its_own_state_rather_than_a_clean_bill():
    state, detail = verdict(True, [], None, "all")
    assert state == "narrowing-not-visible"
    assert "does not report its own permission map" in detail


def test_a_narrowed_token_that_still_covers_the_job_is_not_a_fault():
    assert verdict(True, [], [], "selected")[0] == "narrowed-but-sufficient"


def test_a_wide_token_that_covers_the_job_is_clean():
    assert verdict(True, [], [], "all")[0] == "reach-covers-the-job"


def test_a_dead_token_is_never_reported_as_a_narrowing():
    state, detail = verdict(False, ["acme/docs"], None, None)
    assert state == "token-not-alive"
    assert "does not arise yet" in detail


def test_the_repair_points_at_the_mint_request_and_not_at_the_app():
    text = repair("repos-out-of-reach", ["acme/docs"], None)
    assert "token request" in text
    assert "the App does not change" in text
    assert "mint response" in repair("narrowing-not-visible", [], None)
    assert repair("reach-covers-the-job", [], []) == "nothing. This token is not the constraint."
