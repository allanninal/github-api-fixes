from github_oauth_app_restriction import (
    anonymous_contrast, discriminate, governed, message_signature,
    namespace_shape, read_cost, repair, token_kind, visibility_note,
)

RESTRICTED = ("Although you appear to have the correct authorization "
              "credentials, the acme-corp organization has enabled OAuth App "
              "access restrictions.")


def test_the_shape_is_one_token_reading_two_namespaces():
    state, detail = namespace_shape(200, 403)
    assert state == "personal-ok-org-refused"
    assert "rather than a problem with the credential" in detail
    assert namespace_shape(403, 403)[0] == "refused-everywhere"
    assert namespace_shape(200, 200)[0] == "nothing-refused"


def test_a_saml_header_outranks_every_other_piece_of_evidence():
    # Even with the perfect shape and the exact message, a refusal that names
    # SSO is the other note. A diagnosis of exclusion never beats a statement.
    matched, _ = message_signature(RESTRICTED)
    state, detail = discriminate("personal-ok-org-refused", "required", None,
                                 matched, "OAuth user token")
    assert state == "saml-not-oauth-restriction"
    assert "x-github-sso" in detail


def test_an_accepted_scopes_header_outranks_it_too():
    state, _ = discriminate("personal-ok-org-refused", "", "repo, read:org",
                            True, "OAuth user token")
    assert state == "scope-shaped-refusal"


def test_the_verdict_survives_github_rewording_the_message():
    matched, phrase = message_signature(RESTRICTED)
    assert matched is True and phrase == "oauth app access restrictions"
    confident, _ = discriminate("personal-ok-org-refused", "", None, matched,
                                "OAuth user token")
    assert confident == "oauth-app-restricted"
    # Same shape, message reworded into something unrecognised. The shape still
    # decides; only the confidence drops.
    silent, detail = message_signature("Something entirely new was written here")
    assert silent is False and detail is None
    likely, why = discriminate("personal-ok-org-refused", "", None, silent,
                               "OAuth user token")
    assert likely == "oauth-app-restricted-likely"
    assert "rewords" in why


def test_a_token_refused_below_anonymous_is_blocked_not_underprivileged():
    state, detail = anonymous_contrast(200, 403)
    assert state == "restricted-below-anonymous"
    assert "no token at all succeeds" in detail
    assert anonymous_contrast(404, 403)[0] == "private-to-everyone"
    assert anonymous_contrast(200, 200)[0] == "no-contrast"


def test_only_an_oauth_app_credential_is_governed_by_this_policy():
    ok, _ = governed("OAuth user token")
    assert ok is True
    ok, detail = governed("App installation token")
    assert ok is False and "not issued by an OAuth App" in detail
    state, _ = discriminate("personal-ok-org-refused", "", None, True,
                            "App installation token")
    assert state == "not-an-oauth-app-credential"


def test_a_credential_failing_everywhere_is_never_an_org_policy():
    state, _ = discriminate("refused-everywhere", "", None, True, "OAuth user token")
    assert state == "credential-problem"
    assert "no organization policy is in play" in repair(state, "acme-corp")


def test_the_repair_names_a_person_and_denies_an_api():
    fix = repair("oauth-app-restricted", "acme-corp")
    assert "an owner of acme-corp approves the application" in fix
    assert "no API that grants it" in fix
    assert "does not ask for it" in fix
    assert "GitHub App" in fix


def test_the_visibility_limit_is_part_of_the_output():
    note = visibility_note()
    assert "author cannot see this policy" in note
    assert "member of the" in note


def test_the_credential_type_comes_from_its_prefix():
    assert token_kind("gho_fake") == "OAuth user token"
    assert token_kind("ghs_fake") == "App installation token"
    assert token_kind("nope") == "unknown"


def test_the_anonymous_read_is_not_charged_to_core_quota():
    assert read_cost() == 3
