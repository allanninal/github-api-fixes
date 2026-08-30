import json

from github_token_in_url import (
    audit, credential_params, fingerprint, is_credential, redact,
    same_credential, shape_of, urls_in, verdict)

FAKE = "ghp_FAKE0000000001"
OTHER = "ghs_FAKE0000000002"
BASE = "https://api.github.com/repos/acme/api/issues"


def test_documented_prefixes_are_named():
    assert shape_of(FAKE) == "classic-pat"
    assert shape_of(OTHER) == "app-installation-token"
    assert shape_of("github_pat_FAKE01") == "fine-grained-pat"
    assert shape_of("a" * 40) == "legacy-hex40"


def test_a_short_value_is_not_treated_as_a_credential():
    assert shape_of("30") == "short"
    assert shape_of("") == "short"


def test_a_fingerprint_is_short_stable_and_not_the_value():
    fp = fingerprint(FAKE)
    assert fp.startswith("sha256:")
    assert len(fp) == len("sha256:") + 12
    assert fp == fingerprint(FAKE)
    assert FAKE not in fp


def test_two_sightings_of_one_value_correlate():
    assert same_credential(fingerprint(FAKE), fingerprint(FAKE)) is True
    assert same_credential(fingerprint(FAKE), fingerprint(OTHER)) is False
    assert same_credential(None, fingerprint(FAKE)) is False


def test_the_named_parameter_is_found():
    hits = credential_params("%s?access_token=%s&state=open" % (BASE, FAKE))
    assert len(hits) == 1
    assert hits[0]["param"] == "access_token"
    assert hits[0]["shape"] == "classic-pat"
    assert hits[0]["ignored_by_github"] is True


def test_a_credential_hiding_under_a_harmless_name_is_still_found():
    hits = credential_params("%s?key=%s" % (BASE, FAKE))
    assert len(hits) == 1
    assert hits[0]["param"] == "key"
    assert hits[0]["ignored_by_github"] is False


def test_a_commit_sha_is_not_reported_as_a_legacy_token():
    assert shape_of("a" * 40) == "legacy-hex40"
    assert is_credential("sha", "a" * 40) is False
    assert credential_params("%s?sha=%s" % (BASE, "a" * 40)) == []


def test_a_credential_name_beats_the_git_object_exemption():
    assert is_credential("access_token", "a" * 40) is True


def test_ordinary_parameters_are_left_alone():
    assert credential_params("%s?state=open&per_page=100" % BASE) == []


def test_a_url_with_no_query_is_not_a_finding():
    assert credential_params(BASE) == []


def test_redaction_keeps_the_request_and_drops_the_secret():
    scrubbed = redact("%s?access_token=%s&state=open" % (BASE, FAKE))
    assert FAKE not in scrubbed
    assert "REDACTED" in scrubbed
    assert "state=open" in scrubbed
    assert "/repos/acme/api/issues" in scrubbed


def test_urls_are_pulled_out_of_a_log_line():
    line = '10.0.0.1 - - "GET %s?access_token=%s HTTP/1.1" 200' % (BASE, FAKE)
    found = urls_in(line)
    assert len(found) == 1
    assert found[0].startswith("https://api.github.com/")


def test_nothing_the_script_prints_contains_the_credential():
    entries = [("access.log:12", "%s?access_token=%s" % (BASE, FAKE))]
    findings = audit(entries)
    state, detail = verdict(findings, True, fingerprint(FAKE))
    printed = json.dumps(findings) + state + detail
    assert FAKE not in printed
    assert "sha256:" in printed


def test_a_live_match_demands_revocation():
    findings = audit([("access.log:12", "%s?access_token=%s" % (BASE, FAKE))])
    state, detail = verdict(findings, True, fingerprint(FAKE))
    assert state == "live-credential-in-url"
    assert "Revoke it" in detail
    assert "anonymous" in detail


def test_a_match_on_a_dead_credential_is_historical():
    findings = audit([("access.log:12", "%s?access_token=%s" % (BASE, FAKE))])
    state, detail = verdict(findings, False, fingerprint(FAKE))
    assert state == "dead-credential-in-url"
    assert "historical" in detail


def test_an_unknown_credential_is_assumed_live():
    findings = audit([("access.log:12", "%s?access_token=%s" % (BASE, OTHER))])
    state, detail = verdict(findings, True, fingerprint(FAKE))
    assert state == "credential-in-url"
    assert "treat them as live" in detail


def test_distinct_credentials_are_counted_separately():
    findings = audit([("a", "%s?access_token=%s" % (BASE, FAKE)),
                      ("b", "%s?access_token=%s" % (BASE, FAKE)),
                      ("c", "%s?access_token=%s" % (BASE, OTHER))])
    _, detail = verdict(findings, False, None)
    assert "3 occurrence(s)" in detail
    assert "2 distinct" in detail


def test_a_clean_scan_says_so():
    assert verdict([], True, fingerprint(FAKE))[0] == "no-credential-in-url"
