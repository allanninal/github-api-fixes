from github_outside_collaborator import (
    AFFILIATIONS, counted, has_next_page, header_value, is_member,
    org_endpoint_expectation, org_probe_reading, read_cost, repair,
    repos_in_org, role_verdict, sso_reading, token_class_caveat, verdict,
)

# Obviously fake and far shorter than any real credential.
FINE = "github_pat_FAKE"
CLASSIC = "ghp_FAKE"

ORGS = [{"login": "acme"}, {"login": "Other"}]
REPOS = [
    {"full_name": "acme/payments", "owner": {"login": "acme"}},
    {"full_name": "acme/billing", "owner": {"login": "ACME"}},
    {"full_name": "elsewhere/thing", "owner": {"login": "elsewhere"}},
]

NEXT_LINK = ('<https://api.github.com/user/repos?page=2>; rel="next", '
             '<https://api.github.com/user/repos?page=9>; rel="last"')
LAST_ONLY = '<https://api.github.com/user/repos?page=1>; rel="prev"'


def test_the_partition_is_the_diagnosis():
    # Repositories in the org reached as a collaborator, none as a member.
    state, detail = role_verdict(False, 3, 0)
    assert state == "outside-collaborator"
    assert "No scope grants standing" in detail


def test_each_other_arm_of_the_sort_sends_you_somewhere_else():
    assert role_verdict(True, 0, 12)[0] == "organization-member"
    state, detail = role_verdict(True, 0, 0)
    assert state == "member-with-no-implicit-repos"
    assert "base permission of none" in detail
    state, detail = role_verdict(False, 0, 0)
    assert state == "no-relationship"
    assert "removal rather than a role" in detail


def test_an_announced_partial_list_overrides_the_whole_sort():
    # If GET /user/orgs was explicitly incomplete, the membership answer it
    # rests on is not evidence, whatever the affiliation counts say.
    state, detail = verdict("outside-collaborator", "sso-partial-results")
    assert state == "membership-list-incomplete"
    assert "no membership answer from it can be trusted" in detail
    assert verdict("outside-collaborator", "no-sso-header")[0] == "outside-collaborator"


def test_the_absence_of_the_sso_header_is_read_and_reported():
    state, detail = sso_reading({})
    assert state == "no-sso-header"
    assert "The SAML note is about the case where GitHub does tell you." in detail
    partial = {"X-GitHub-SSO": "partial-results; organizations=1,2"}
    assert sso_reading(partial)[0] == "sso-partial-results"
    assert sso_reading({"x-github-sso": "required; url=https://example"})[0] == (
        "sso-header-present")


def test_membership_and_ownership_comparisons_are_case_insensitive():
    assert is_member(ORGS, "ACME") is True
    assert is_member(ORGS, "nope") is False
    assert is_member([], "acme") is False
    assert repos_in_org(REPOS, "acme") == ["acme/payments", "acme/billing"]
    assert repos_in_org(REPOS, "elsewhere") == ["elsewhere/thing"]


def test_a_count_says_when_it_is_only_a_floor():
    assert has_next_page(NEXT_LINK) is True
    assert has_next_page(LAST_ONLY) is False
    assert has_next_page(None) is False
    total, exact, phrase = counted(["a", "b"], True)
    assert (total, exact, phrase) == (2, False, "at least 2")
    assert counted(["a", "b"], False) == (2, True, "2")
    assert counted([], False) == (0, True, "0")


def test_the_endpoint_that_does_not_fail_is_named_as_the_dangerous_one():
    expectation = org_endpoint_expectation("outside-collaborator")
    assert "under-reports" in expectation["org-repos-listing"]
    assert "404 rather than 403" in expectation["members-and-teams"]
    # And the one call that would name the condition needs access this
    # account does not have, which is the joke at the centre of the note.
    assert "organization read access" in expectation["outside-collaborators-listing"]
    assert "answer for a member" in org_endpoint_expectation(
        "organization-member")["members-and-teams"]


def test_the_pair_of_readings_is_the_sentence_for_the_ticket():
    state, detail = org_probe_reading(200, 404)
    assert state == "repo-yes-org-no"
    assert "put in the ticket" in detail
    assert org_probe_reading(200, 200)[0] == "org-reachable"
    assert org_probe_reading(200, 403)[0] == "org-refused-not-hidden"
    assert org_probe_reading(200, None)[0] == "org-not-probed"


def test_the_documented_fine_grained_gap_can_invert_the_answer():
    state, detail = token_class_caveat(FINE)
    assert state == "fine-grained-gap"
    assert "outside or repository collaborator" in detail
    assert token_class_caveat(CLASSIC)[0] == "classic-token"
    assert token_class_caveat("")[0] == "class-not-recognised"


def test_the_repair_offers_two_choices_and_takes_neither():
    fix = repair("outside-collaborator", "acme", "dana-integration")
    assert "ask an owner of acme" in fix
    assert "work at repository scope" in fix
    assert "Nothing here invites anybody" in fix
    assert "default repository permission" in repair(
        "member-with-no-implicit-repos", "acme", "dana")


def test_the_read_cost_and_the_affiliation_names():
    assert read_cost(False) == 3
    assert read_cost(True) == 4
    assert AFFILIATIONS == ("owner", "collaborator", "organization_member")


def test_header_reads_survive_whatever_case_the_client_gives_them():
    assert header_value({"Link": "x"}, "link") == "x"
    assert header_value({"link": "x"}, "LINK") == "x"
    assert header_value(None, "link") is None
