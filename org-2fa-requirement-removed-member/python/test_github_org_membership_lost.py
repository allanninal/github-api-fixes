from github_org_membership_lost import (
    combine, listed_in_orgs, membership_state, own_two_factor,
    question_answered, read_cost, repair, requirement_state, symptom,
    token_health,
)


def test_the_redirect_is_the_finding_and_not_an_error():
    state, detail = membership_state(302)
    assert state == "requester-not-a-member"
    assert "that is the removal" in detail
    assert membership_state(204)[0] == "member"
    assert membership_state(404)[0] == "not-a-member"
    assert membership_state(418)[0] == "unclear"


def test_following_the_redirect_answers_a_different_question():
    state, detail = question_answered(True)
    assert state == "public-membership-instead"
    assert "publicly listed" in detail
    assert question_answered(False)[0] == "membership"


def test_an_absent_requirement_field_is_unreadable_not_false():
    # The field is returned to callers with org access, and being removed is
    # what takes that away. Reading absence as False would report the removal
    # as unexplained precisely when the removal is the explanation.
    assert requirement_state({}) is None
    assert requirement_state({"two_factor_requirement_enabled": None}) is None
    assert requirement_state({"two_factor_requirement_enabled": True}) is True
    assert requirement_state({"two_factor_requirement_enabled": False}) is False


def test_an_absent_two_factor_field_does_not_invent_a_violation():
    assert own_two_factor({"login": "octobot"}) is None
    assert own_two_factor({"two_factor_authentication": False}) is False
    assert own_two_factor({"two_factor_authentication": True}) is True
    assert own_two_factor(None) is None


def test_the_removal_and_its_motive_are_reported_together():
    state, detail = combine("requester-not-a-member", True, None)
    assert state == "not-a-member-2fa-required"
    assert "cause and its motive" in detail
    assert "indistinguishable" in detail


def test_an_unreadable_motive_is_still_a_finding():
    state, detail = combine("requester-not-a-member", None, None)
    assert state == "not-a-member-motive-unreadable"
    assert "losing that access is what this finding is" in detail


def test_a_removal_with_no_requirement_is_sent_to_the_audit_log():
    state, detail = combine("not-a-member", False, None)
    assert state == "not-a-member-no-requirement"
    assert "audit log" in detail


def test_a_member_with_2fa_off_is_flagged_before_anything_breaks():
    state, detail = combine("member", True, False)
    assert state == "member-at-risk"
    assert "has not happened yet" in detail


def test_a_compliant_member_is_sent_somewhere_else():
    assert combine("member", True, True)[0] == "member-compliant"
    assert combine("member", True, None)[0] == "member-compliance-unreadable"
    assert combine("member", False, True)[0] == "member-no-requirement"
    assert combine("membership-unreadable", True, True)[0] == "membership-unreadable"


def test_the_symptom_is_404_and_not_403():
    text = symptom("not-a-member-2fa-required")
    assert "404, not 403" in text
    assert "Public repositories keep answering" in text
    assert "nothing yet" in symptom("member-at-risk")


def test_a_healthy_token_is_stated_so_the_search_can_move_on():
    state, detail = token_health(200)
    assert state == "healthy"
    assert "end that search early" in detail
    assert token_health(401)[0] == "rejected"


def test_user_orgs_is_corroboration_and_matches_case_insensitively():
    orgs = [{"login": "ACME"}, {"login": "other"}]
    assert listed_in_orgs(orgs, "acme") is True
    assert listed_in_orgs(orgs, "missing") is False
    assert listed_in_orgs([], "acme") is False


def test_the_repair_offers_the_change_that_cannot_happen_again():
    fix = repair("not-a-member-2fa-required", "acme", "octobot")
    assert "octobot" in fix and "acme" in fix
    assert "GitHub App installation" in fix
    assert "re-invites anybody" in fix
    assert "before the requirement is enforced" in repair(
        "member-at-risk", "acme", "octobot")


def test_the_run_costs_four_reads():
    assert read_cost() == 4
