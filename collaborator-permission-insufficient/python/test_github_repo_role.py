from github_repo_role import (
    ACTION_MINIMUM, ROLES, blocked_actions, can, deficit, read_cost, repair,
    role_from_collaborator, role_from_permissions, role_rank, scope_list,
    scopes_are_the_ceiling, token_kind, verdict,
)

READ_ONLY = {"admin": False, "maintain": False, "push": False,
             "triage": False, "pull": True}
TRIAGE = {"admin": False, "maintain": False, "push": False,
          "triage": True, "pull": True}
WRITE = {"admin": False, "maintain": False, "push": True,
         "triage": True, "pull": True}
ADMIN = {"admin": True, "maintain": True, "push": True,
         "triage": True, "pull": True}


def test_the_hierarchy_runs_weakest_to_strongest():
    assert ROLES == ("none", "read", "triage", "write", "maintain", "admin")
    assert role_rank("read") < role_rank("triage") < role_rank("write")
    assert role_rank("write") < role_rank("maintain") < role_rank("admin")
    assert role_rank("nonsense") == -1


def test_the_role_is_the_highest_true_flag():
    assert role_from_permissions(READ_ONLY) == "read"
    assert role_from_permissions(TRIAGE) == "triage"
    assert role_from_permissions(WRITE) == "write"
    assert role_from_permissions(ADMIN) == "admin"


def test_an_absent_permissions_object_is_unreported_not_none():
    # An unauthenticated read carries no permissions object at all. Reporting
    # that as "no access" would be a different and wrong finding.
    assert role_from_permissions({}) == "unreported"
    assert role_from_permissions(None) == "unreported"
    assert role_from_permissions({"admin": False, "pull": False}) == "none"


def test_read_explains_every_refused_write_in_one_flag():
    assert READ_ONLY["push"] is False
    assert can("read", "merge-pull-request") is False
    assert can("read", "push-branch") is False
    assert can("read", "read-code") is True


def test_labelling_needs_triage_and_not_write():
    # The role people are told to ask for is usually higher than the one they
    # need, and this is the row that shows it.
    assert ACTION_MINIMUM["label-issue"] == "triage"
    assert can("triage", "label-issue") is True
    assert can("triage", "merge-pull-request") is False
    assert deficit("triage", "merge-pull-request") == 1


def test_the_deficit_counts_roles_not_booleans():
    assert deficit("read", "merge-pull-request") == 2
    assert deficit("read", "add-collaborator") == 4
    assert deficit("admin", "merge-pull-request") == 0
    assert deficit("read", "not-an-action") is None


def test_the_legacy_permission_field_rounds_two_roles_away():
    exact, is_exact, _ = role_from_collaborator(
        {"permission": "write", "role_name": "maintain"})
    assert (exact, is_exact) == ("maintain", True)
    rounded, is_exact, note = role_from_collaborator({"permission": "write"})
    assert rounded == "write" and is_exact is False
    assert "rounds maintain to write" in note
    # A triager comes back as read through the legacy field, which would deny a
    # label they can actually apply.
    assert role_from_collaborator({"permission": "read"})[0] == "read"
    assert role_from_collaborator({"role_name": "triage"})[0] == "triage"


def test_a_custom_org_role_is_named_and_not_priced():
    role, is_exact, note = role_from_collaborator(
        {"permission": "read", "role_name": "security-auditor"})
    assert role == "custom:security-auditor"
    assert is_exact is False and "custom organization role" in note
    state, detail = verdict(role, "merge-pull-request")
    assert state == "custom-role"
    assert "does not price" in detail or "not priced" in detail


def test_a_repo_scope_beside_a_read_role_is_the_headline():
    state, detail = scopes_are_the_ceiling(
        "read", ["repo", "workflow"], "classic PAT", "merge-pull-request")
    assert state == "scopes-are-not-the-ceiling"
    assert "cannot change this answer" in detail


def test_a_fine_grained_token_has_no_scopes_to_widen():
    state, detail = scopes_are_the_ceiling(
        "read", None, "fine-grained PAT", "merge-pull-request")
    assert state == "no-scopes-to-widen"
    assert "nothing to widen" in detail


def test_a_narrow_scope_and_a_low_role_are_both_reported():
    state, detail = scopes_are_the_ceiling(
        "read", ["public_repo"], "classic PAT", "merge-pull-request")
    assert state == "two-gates-open"
    assert "both" in detail


def test_a_sufficient_role_sends_the_reader_to_the_credential():
    state, _ = scopes_are_the_ceiling(
        "write", ["repo"], "classic PAT", "merge-pull-request")
    assert state == "not-the-question"
    assert verdict("write", "merge-pull-request")[0] == "role-sufficient"


def test_the_verdict_and_its_repair_hang_together():
    state, detail = verdict("read", "merge-pull-request")
    assert state == "role-insufficient"
    assert "2 role(s) higher" in detail
    fix = repair(state, "read", "merge-pull-request", "octobot")
    assert "octobot" in fix and "'write'" in fix
    assert "never its source" in fix


def test_no_access_is_kept_apart_from_a_low_role():
    state, detail = verdict("none", "merge-pull-request")
    assert state == "no-access"
    assert "404" in detail
    assert verdict("unreported", "merge-pull-request")[0] == "role-unreported"


def test_the_blocked_list_grows_as_the_role_shrinks():
    assert "merge-pull-request" in blocked_actions("read")
    assert "label-issue" in blocked_actions("read")
    assert "label-issue" not in blocked_actions("triage")
    assert blocked_actions("admin") == []


def test_scopes_absent_and_scopes_empty_are_different_readings():
    assert scope_list(None) is None
    assert scope_list("") == []
    assert scope_list("repo, workflow") == ["repo", "workflow"]


def test_the_credential_type_comes_from_its_prefix():
    assert token_kind("ghp_x") == "classic PAT"
    assert token_kind("github_pat_x") == "fine-grained PAT"
    assert token_kind("ghs_x") == "App installation token"
    assert token_kind("nope") == "unknown"


def test_the_run_costs_two_reads_or_three():
    assert read_cost() == 2
    assert read_cost(True) == 3
