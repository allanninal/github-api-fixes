from github_api_host import (
    DOTCOM_API_HOST, FAMILIES, GHES_REST_SUFFIX, agreement, content_is_html,
    family_from_meta, family_from_url, host_of, identity_check,
    normalise_base, read_cost, repair, served_host_from_root,
    token_shape_is_no_evidence, verdict,
)

DOTCOM_META = {"verifiable_password_authentication": False,
               "hooks": ["192.30.252.0/22"], "api": ["192.30.252.0/22"]}
GHES_META = {"verifiable_password_authentication": True,
             "installed_version": "3.14.2", "hooks": ["10.0.0.0/8"]}
DOTCOM_ROOT = {"current_user_url": "https://api.github.com/user",
               "repository_url": "https://api.github.com/repos/{owner}/{repo}"}
GHES_ROOT = {"current_user_url": "https://github.acme.internal/api/v3/user"}

# Obviously fake and far shorter than any real credential.
FINE = "github_pat_FAKE"


def test_the_three_host_families_are_read_off_the_url():
    assert family_from_url("https://api.github.com")[0] == "dotcom"
    assert family_from_url("https://github.acme.internal/api/v3")[0] == "enterprise-server"
    assert family_from_url("https://api.octocorp.ghe.com")[0] == (
        "enterprise-cloud-data-residency")
    assert set(FAMILIES) >= {"dotcom", "enterprise-server",
                             "enterprise-cloud-data-residency"}


def test_the_missing_api_prefix_is_named_as_its_own_failure():
    state, detail = family_from_url("https://github.acme.internal")
    assert state == "web-host-not-api"
    assert GHES_REST_SUFFIX in detail
    state, detail = family_from_url("https://github.com")
    assert state == "web-host-not-api"
    assert DOTCOM_API_HOST in detail
    assert family_from_url("not a url")[0] == "unknown"


def test_the_graphql_path_is_still_the_appliance():
    assert family_from_url("https://github.acme.internal/api/graphql")[0] == (
        "enterprise-server")
    assert normalise_base("https://api.github.com///") == "https://api.github.com"
    assert host_of("https://API.GitHub.com/user") == "api.github.com"
    assert host_of("nonsense") is None


def test_installed_version_is_the_discriminator():
    state, detail = family_from_meta(200, "application/json", GHES_META)
    assert state == "enterprise-server"
    assert "3.14.2" in detail
    state, detail = family_from_meta(200, "application/json", DOTCOM_META)
    assert state == "dotcom-or-enterprise-cloud"
    assert "cannot be separated here" in detail


def test_html_from_an_api_base_is_the_silent_one():
    state, detail = family_from_meta(200, "text/html; charset=utf-8", None)
    assert state == "web-host-not-api"
    assert "reports success" in detail
    assert content_is_html("text/html") is True
    assert content_is_html("application/json") is False
    assert family_from_meta(401, "application/json", None)[0] == "meta-unreadable"
    assert family_from_meta(200, "application/json", {"unrelated": 1})[0] == (
        "meta-unreadable")


def test_the_root_map_names_the_host_that_actually_answered():
    host, detail = served_host_from_root(GHES_ROOT)
    assert host == "github.acme.internal"
    assert "current_user_url" in detail
    assert served_host_from_root(DOTCOM_ROOT)[0] == "api.github.com"
    assert served_host_from_root({})[0] is None
    assert served_host_from_root({"x": 1})[0] is None


def test_a_dotcom_base_against_an_appliance_is_the_headline():
    state, detail = agreement("dotcom", "enterprise-server", "api.github.com",
                              "api.github.com")
    assert state == "wrong-host-family"
    assert "different installations" in detail


def test_an_appliance_base_answered_by_something_else_is_caught_too():
    assert agreement("enterprise-server", "dotcom-or-enterprise-cloud",
                     "github.acme.internal", "github.acme.internal")[0] == (
        "wrong-host-family")


def test_a_redirect_is_the_reading_configuration_cannot_give_you():
    state, detail = agreement("dotcom", "dotcom-or-enterprise-cloud",
                              "api.github.com", "api.ghe.example")
    assert state == "served-elsewhere"
    assert "reading the configuration would never have caught" in detail


def test_agreement_reports_agreement():
    assert agreement("dotcom", "dotcom-or-enterprise-cloud", "api.github.com",
                     "api.github.com")[0] == "agrees"
    assert agreement("enterprise-server", "meta-unreadable",
                     "github.acme.internal", None)[0] == "host-unidentified"
    assert agreement("web-host-not-api", "web-host-not-api", "github.com",
                     None)[0] == "no-api-prefix"


def test_a_credential_from_the_other_installation_is_stated_plainly():
    state, detail = identity_check(401, None, None, "dana", "github.acme.internal")
    assert state == "credential-not-of-this-host"
    assert "it is not a token at all" in detail
    state, detail = identity_check(200, "someone-else",
                                   "https://github.acme.internal/someone-else",
                                   "dana", "github.acme.internal")
    assert state == "wrong-account"
    assert "different installation" in detail
    assert identity_check(0, None, None, None, None)[0] == "not-checked"
    assert identity_check(503, None, None, None, None)[0] == "identity-unreadable"


def test_the_identity_passes_when_the_account_and_the_host_agree():
    state, _ = identity_check(200, "dana",
                              "https://github.acme.internal/dana", "dana",
                              "github.acme.internal")
    assert state == "identity-as-expected"
    # api.github.com serving objects whose html_url is github.com is normal:
    # one host name is a suffix of the other, so it is not a mismatch.
    assert identity_check(200, "dana", "https://github.com/dana", "dana",
                          "api.github.com")[0] == "identity-as-expected"


def test_a_token_prefix_names_a_class_and_never_an_installation():
    state, detail = token_shape_is_no_evidence(FINE)
    assert state == "class-known-host-unknown"
    assert "never the installation" in detail
    assert token_shape_is_no_evidence("")[0] == "class-unknown"


def test_the_verdict_and_the_repair_are_about_configuration():
    assert verdict("wrong-host-family", "not-checked")[0] == "wrong-installation"
    assert verdict("no-api-prefix", "not-checked")[0] == "no-api-prefix"
    assert verdict("served-elsewhere", "not-checked")[0] == "redirected-elsewhere"
    assert verdict("agrees", "credential-not-of-this-host")[0] == (
        "credential-from-another-host")
    assert verdict("agrees", "identity-as-expected")[0] == "host-as-configured"
    fix = repair("wrong-installation", "https://api.github.com")
    assert "set the base URL explicitly" in fix
    assert "letting a library default decide" in fix
    assert "startup assertion" in repair("host-as-configured", "x")


def test_the_host_check_needs_no_credential():
    assert read_cost(False) == (2, 2)
    assert read_cost(True) == (3, 2)
