from datetime import datetime, timezone

from github_pat_pending_approval import (
    classify, days_pending, find_request, header_is_not_the_discriminator,
    oauth_wording, probe_shape, read_cost, repair, token_kind,
)

NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)

PERSONAL_OK = [("user", 200), ("repositories", 200), ("issues", 200)]
ORG_ALL_REFUSED = [("repositories", 403), ("issues", 403), ("members", 403)]
ORG_ONE_FAMILY = [("repositories", 200), ("issues", 403), ("members", 200)]


def test_an_owner_shaped_failure_refuses_every_family_in_one_namespace():
    shape, detail = probe_shape(PERSONAL_OK, ORG_ALL_REFUSED)
    assert shape == "owner-shaped"
    assert "the gate is the resource owner" in detail
    state, why = classify(shape, "fine-grained PAT", False, False)
    assert state == "pending-org-approval"
    assert "on paper and none in practice" in why


def test_an_endpoint_shaped_failure_follows_one_family_everywhere():
    # The same token, the same organization, and a completely different cause:
    # issues are refused in both namespaces, so the permission is what is short.
    personal = [("user", 200), ("repositories", 200), ("issues", 403)]
    shape, detail = probe_shape(personal, ORG_ONE_FAMILY)
    assert shape == "endpoint-shaped"
    assert "issues" in detail
    assert classify(shape, "fine-grained PAT", False, False)[0] == "permission-shaped"


def test_one_organization_family_is_not_enough_to_name_a_cause():
    shape, detail = probe_shape(PERSONAL_OK, [("repositories", 403)])
    assert shape == "insufficient-evidence"
    assert "one family cannot show" in detail
    assert classify(shape, "fine-grained PAT", False, False)[0] == "undetermined"


def test_a_failing_personal_namespace_is_a_credential_not_a_queue():
    personal = [("user", 200), ("repositories", 403), ("issues", 403)]
    dead = [("user", 403), ("repositories", 403), ("issues", 403)]
    assert probe_shape(dead, ORG_ALL_REFUSED)[0] == "credential-shaped"
    # A partly-failing personal namespace is not owner-shaped either.
    assert probe_shape(personal, ORG_ALL_REFUSED)[0] != "owner-shaped"


def test_a_clean_run_says_nothing_is_waiting():
    ok = [("repositories", 200), ("issues", 200), ("members", 200)]
    assert probe_shape(PERSONAL_OK, ok)[0] == "nothing-refused"
    assert classify("nothing-refused", "fine-grained PAT", False, False)[0] == "not-blocked"


def test_the_neighbouring_gates_outrank_the_shape_because_they_announce_themselves():
    assert classify("owner-shaped", "fine-grained PAT", True, False)[0] == "saml-enforcement"
    assert classify("owner-shaped", "fine-grained PAT", False, True)[0] == "oauth-app-restriction"
    assert oauth_wording("the acme-corp organization has enabled OAuth App "
                         "access restrictions") is True
    assert oauth_wording("Resource not accessible by personal access token") is False


def test_a_classic_token_is_never_sent_to_the_approval_queue():
    state, detail = classify("owner-shaped", "classic PAT", False, False)
    assert state == "not-a-fine-grained-token"
    assert "different repair" in detail


def test_the_permissions_header_is_stated_not_to_be_the_discriminator():
    note = header_is_not_the_discriminator()
    assert "never what the token holds" in note
    assert "cannot settle this" in note


def test_the_repair_prints_the_approval_and_forbids_a_second_token():
    fix = repair("pending-org-approval", "acme-corp")
    assert "an owner of acme-corp approves the waiting request" in fix
    assert "does not approve it and does not ask for it" in fix
    assert "Do not create a replacement token" in fix


def test_the_pending_request_is_matched_on_a_public_login():
    pending = [{"id": 42, "owner": {"login": "Dana"},
                "repository_selection": "all",
                "created_at": "2026-08-25T09:00:00Z"}]
    found = find_request(pending, "dana")
    assert found["id"] == 42
    assert find_request(pending, "someone-else") is None
    assert days_pending(found["created_at"], NOW) == 6
    assert days_pending(None, NOW) is None
    assert days_pending("not a date", NOW) is None


def test_the_credential_type_comes_from_its_prefix():
    assert token_kind("github_pat_x") == "fine-grained PAT"
    assert token_kind("ghp_fake") == "classic PAT"
    assert token_kind("nope") == "unknown"


def test_the_run_costs_six_reads_or_seven():
    assert read_cost() == 6
    assert read_cost(True) == 7
