from github_sso_required import (
    FORM_PARTIAL, FORM_REQUIRED, authorize_url, click_verdict,
    enforcement_signature, parse_sso_header, read_cost, repair, token_kind,
    which_sso_note,
)

REQUIRED_HEADER = ("required; url=https://github.com/orgs/acme-corp/sso"
                   "?authorization_request=AB12CD")
PARTIAL_HEADER = "partial-results; organizations=21955855,20582480"


def test_the_required_form_keeps_its_whole_url():
    # The URL contains = characters of its own. Splitting on every one of them
    # is the bug that turns the repair into a fragment.
    sso = parse_sso_header(REQUIRED_HEADER)
    assert sso["form"] == FORM_REQUIRED
    assert sso["url"].endswith("?authorization_request=AB12CD")
    assert sso["url"].startswith("https://github.com/orgs/acme-corp/sso")


def test_the_partial_form_is_a_different_finding_not_a_refusal():
    sso = parse_sso_header(PARTIAL_HEADER)
    assert sso["form"] == FORM_PARTIAL
    assert sso["organizations"] == ["21955855", "20582480"]
    state, detail = enforcement_signature(200, 200, sso)
    assert state == "partial-results-not-a-refusal"
    assert "Nothing was refused" in detail or "nothing was refused" in detail


def test_an_absent_header_parses_without_inventing_a_form():
    assert parse_sso_header(None) == {"form": None, "url": None,
                                      "organizations": []}
    assert parse_sso_header("")["form"] is None
    assert parse_sso_header("required")["url"] is None


def test_the_signature_is_a_pair_of_reads_not_one_status():
    sso = parse_sso_header(REQUIRED_HEADER)
    assert enforcement_signature(200, 403, sso)[0] == "sso-authorization-required"
    # SAML can mask as a bare 404, so the same pair with a 404 is the same
    # finding rather than a missing organization.
    assert enforcement_signature(200, 404, sso)[0] == "sso-authorization-required"


def test_a_misspelled_org_is_never_reported_as_saml():
    empty = parse_sso_header(None)
    state, detail = enforcement_signature(404, 404, empty)
    assert state == "organization-unreadable"
    assert "spelling" in detail


def test_a_refusal_without_the_header_is_handed_elsewhere():
    empty = parse_sso_header(None)
    state, detail = enforcement_signature(200, 403, empty)
    assert state == "refused-without-sso-header"
    assert "did not attribute" in detail


def test_a_clean_read_is_reported_as_authorized_today():
    empty = parse_sso_header(None)
    assert enforcement_signature(200, 200, empty)[0] == "no-refusal-to-explain"
    # The header on a success is advance warning rather than nothing.
    warned = parse_sso_header(REQUIRED_HEADER)
    assert enforcement_signature(200, 200, warned)[0] == "sso-required-on-a-success"


def test_the_url_falls_back_to_the_address_that_never_expires():
    url, source = authorize_url(parse_sso_header(REQUIRED_HEADER), "acme-corp")
    assert "authorization_request=AB12CD" in url and "short-lived" in source
    url, source = authorize_url(parse_sso_header(None), "acme-corp")
    assert url == "https://github.com/orgs/acme-corp/sso"
    assert "stable" in source


def test_an_installation_token_is_never_sent_to_the_sso_page():
    helps, detail = click_verdict("App installation token")
    assert helps is False
    assert "not subject to" in detail
    fix = repair("sso-authorization-required", "acme-corp",
                 "https://github.com/orgs/acme-corp/sso",
                 "App installation token", False)
    assert "do not send anyone to the SSO page" in fix


def test_a_fine_grained_token_is_routed_to_the_approval_note():
    helps, detail = click_verdict("fine-grained PAT")
    assert helps is False
    assert "waiting for an owner" in detail


def test_a_classic_token_gets_the_click_and_the_warning_about_widening():
    helps, detail = click_verdict("classic PAT")
    assert helps is True
    assert "Reminting it wider cannot change this answer" in detail


def test_the_repair_says_the_click_belongs_to_a_person():
    fix = repair("sso-authorization-required", "acme-corp",
                 "https://github.com/orgs/acme-corp/sso", "classic PAT", False)
    assert "does not open it and must not" in fix
    assert "installation token" in fix


def test_a_prior_success_points_at_the_lapse_note_instead():
    state, detail = which_sso_note(True)
    assert state == "session-lapse"
    assert "lapsed" in detail
    assert which_sso_note(False)[0] == "first-authorization"
    fix = repair("sso-authorization-required", "acme-corp",
                 "https://github.com/orgs/acme-corp/sso", "classic PAT", True)
    assert "again" in fix


def test_the_credential_type_comes_from_its_prefix_locally():
    # Obviously fake, and short. Nothing in this suite is a real credential.
    assert token_kind("ghp_fake") == "classic PAT"
    assert token_kind("gho_fake") == "OAuth user token"
    assert token_kind("ghs_fake") == "App installation token"
    assert token_kind("github_pat_x") == "fine-grained PAT"
    assert token_kind("nope") == "unknown"


def test_the_run_costs_three_reads():
    assert read_cost() == 3
