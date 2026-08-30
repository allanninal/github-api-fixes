from github_branch_protection_audit import (
    REQUESTS_PER_BRANCH, coverage, instrument_verdict, is_absence,
    push_allowlist, read_cost, refused_by_rules, refused_writes, repair,
    rulesets_named, split_target, verdict, visibility,
)

ADMIN_403 = "Must have admin rights to Repository."
ABSENT_404 = "Branch not protected"

PROTECTION = {
    "required_pull_request_reviews": {"required_approving_review_count": 2},
    "required_status_checks": {"strict": True, "contexts": ["build", "lint"]},
    "enforce_admins": {"enabled": True},
    "restrictions": {"users": [{"login": "release-bot"}],
                     "teams": [{"slug": "platform"}], "apps": []},
    "required_signatures": {"enabled": False},
    "allow_force_pushes": {"enabled": False},
    "allow_deletions": {"enabled": False},
}

RULES = [
    {"type": "pull_request", "ruleset_id": 42, "ruleset_source": "acme"},
    {"type": "non_fast_forward", "ruleset_id": 42, "ruleset_source": "acme"},
]


def test_a_403_is_never_evidence_that_a_branch_is_unprotected():
    assert is_absence(403, ADMIN_403) is False
    # Not even when the refusal happens to carry the absence wording.
    assert is_absence(403, ABSENT_404) is False
    assert visibility(403, ADMIN_403) == "admin-required"


def test_only_a_404_that_names_the_reason_is_an_absence():
    assert is_absence(404, ABSENT_404) is True
    assert is_absence(404, "Not Found") is False
    assert visibility(404, ABSENT_404) == "not-protected"
    assert visibility(404, "Not Found") == "ambiguous-404"


def test_the_three_outcomes_stay_three():
    assert visibility(200, "") == "readable"
    assert visibility(500, "") == "unknown"
    assert visibility(None, "") == "unknown"


def test_a_refused_read_on_a_protected_branch_is_protected_and_unmeasured():
    state, detail = verdict(True, 403, ADMIN_403, RULES)
    assert state == "protected-rules-hidden"
    assert "not readable by this token" in detail
    assert "2 ruleset rule(s)" in detail
    assert "administration: read" in repair(state)


def test_a_protected_branch_with_readable_rules_is_the_measured_case():
    state, _ = verdict(True, 200, "", [])
    assert state == "protected-rules-readable"


def test_an_unprotected_branch_needs_both_readings_to_agree():
    assert verdict(False, 404, ABSENT_404, [])[0] == "unprotected-confirmed"
    state, detail = verdict(False, 403, ADMIN_403, [])
    assert state == "unprotected-by-flag"
    assert "visible without admin" in detail
    assert "already see" in repair(state)


def test_a_ruleset_protects_a_branch_that_reports_protected_false():
    state, detail = verdict(False, 404, ABSENT_404, RULES)
    assert state == "ruleset-only"
    assert "from a ruleset" in detail
    assert "read the ruleset" in repair(state)


def test_a_branch_that_did_not_come_back_is_not_a_protection_finding():
    state, _ = verdict(None, 404, "Not Found", [])
    assert state == "branch-unreadable"
    assert "triage the repository" in repair(state)


def test_the_refusals_are_derived_from_fields_not_from_a_push():
    lines = refused_writes(PROTECTION)
    assert "a direct push is refused: 2 approving review(s) are required through a pull request" in lines
    assert "a merge is refused until 2 status check(s) pass" in lines
    assert "a merge is refused while the branch is behind its base" in lines
    assert "administrators are not exempt from any of the above" in lines
    assert "a push is refused for everyone except 2 listed actor(s)" in lines
    assert "a force push is refused" in lines
    assert "deleting the branch is refused" in lines
    assert refused_writes(None) == []


def test_an_unsigned_commit_rule_is_only_reported_when_enabled():
    assert "an unsigned commit is refused" not in refused_writes(PROTECTION)
    signed = dict(PROTECTION, required_signatures={"enabled": True})
    assert "an unsigned commit is refused" in refused_writes(signed)


def test_a_locked_branch_refuses_everything():
    locked = dict(PROTECTION, lock_branch={"enabled": True})
    assert "the branch is locked, so every write is refused" in refused_writes(locked)


def test_the_ruleset_listing_describes_the_same_refusals_without_admin():
    lines = refused_by_rules(RULES)
    assert "a pull request is required, so a direct push to this branch is refused" in lines
    assert "non-fast-forward updates are blocked, so a force push is refused" in lines
    assert refused_by_rules([]) == []
    assert refused_by_rules("not a list") == []
    assert rulesets_named(RULES) == ["acme"]


def test_the_allowlist_reports_names_and_nothing_else():
    assert push_allowlist(PROTECTION) == ["user:release-bot", "team:platform"]
    assert push_allowlist({}) == []
    assert push_allowlist(None) == []


def test_an_unknown_row_never_becomes_an_unprotected_row():
    counts = coverage(["protected-rules-hidden", "unknown", "branch-unreadable",
                       "unprotected-confirmed", "protected-rules-readable"])
    assert counts == {"protected": 2, "readable_in_detail": 1,
                      "unprotected": 1, "unknown": 2}
    state, detail = instrument_verdict(counts)
    assert state == "instrument-gap"
    assert "2 of 5" in detail


def test_a_sweep_with_no_detail_says_so_rather_than_claiming_a_measurement():
    counts = coverage(["protected-rules-hidden", "protected-rules-hidden"])
    state, detail = instrument_verdict(counts)
    assert state == "coverage-only"
    assert "detail is absent" in detail
    assert instrument_verdict({})[0] == "no-rows"
    assert instrument_verdict(coverage(["protected-rules-readable"]))[0] == "measured"


def test_targets_and_cost_are_worked_out_before_anything_is_fetched():
    assert split_target("acme/platform-api:release/2.1") == (
        "acme", "platform-api", "release/2.1")
    assert split_target("acme/platform-api") == ("acme", "platform-api", "main")
    assert split_target("platform-api") is None
    assert split_target("") is None
    assert REQUESTS_PER_BRANCH == 3
    assert read_cost(["a", "b"]) == 6
    assert read_cost([]) == 0
    assert read_cost(None) == 0
