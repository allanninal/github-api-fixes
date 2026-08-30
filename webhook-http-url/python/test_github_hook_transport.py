from github_hook_transport import (
    classify, host_of, is_private_host, looks_compliant, repair, safe_url,
    scheme_of, summarize,
)

OPEN = {"id": 1, "config": {"url": "http://hooks.acme.io/github",
                            "insecure_ssl": "0", "secret": "********",
                            "content_type": "json"}}
LOCAL = {"id": 2, "config": {"url": "http://localhost:3000/hooks",
                             "insecure_ssl": "0"}}
TLS = {"id": 3, "config": {"url": "https://hooks.acme.io/github",
                           "insecure_ssl": "0", "secret": "********"}}
UNVERIFIED = {"id": 4, "config": {"url": "https://hooks.acme.io/github",
                                  "insecure_ssl": "1", "secret": "********"}}


def test_plaintext_on_a_routable_host_is_the_finding():
    state, detail = classify(OPEN)
    assert state == "plaintext"
    assert "unencrypted connection" in detail
    assert "signing payloads on an open channel" in repair(state, OPEN)


def test_plaintext_on_localhost_is_a_dead_hook_not_a_leak():
    state, detail = classify(LOCAL)
    assert state == "plaintext-unreachable"
    assert "never delivered anything" in detail
    assert "delete this hook" in repair(state, LOCAL)


def test_the_certificate_question_is_handed_to_the_other_note():
    state, detail = classify(UNVERIFIED)
    assert state == "encrypted-unverified"
    assert "different question" in detail
    assert classify(TLS)[0] == "encrypted"


def test_the_compliant_looking_field_is_named_in_the_finding():
    assert looks_compliant(OPEN)
    assert not looks_compliant(TLS)
    assert not looks_compliant(UNVERIFIED)
    assert "what a hook with no TLS at all always reads" in classify(OPEN)[1]


def test_a_plaintext_hook_with_no_insecure_ssl_field_is_still_the_finding():
    hook = {"id": 5, "config": {"url": "http://hooks.acme.io/github"}}
    assert not looks_compliant(hook)
    assert classify(hook)[0] == "plaintext"


def test_the_private_ranges_stop_where_they_should():
    assert is_private_host("10.0.0.1")
    assert is_private_host("192.168.1.7")
    assert is_private_host("172.16.0.1")
    assert is_private_host("172.31.255.254")
    assert is_private_host("127.0.0.1")
    assert is_private_host("169.254.169.254")
    assert not is_private_host("172.15.0.1")
    assert not is_private_host("172.32.0.1")
    assert not is_private_host("8.8.8.8")
    assert not is_private_host("hooks.acme.io")


def test_local_names_and_ipv6_loopback_count_as_unreachable():
    assert is_private_host("localhost")
    assert is_private_host("build-01.internal")
    assert is_private_host("printer.local")
    assert is_private_host("::1")
    assert is_private_host("fd00::1")
    assert not is_private_host("")
    assert not is_private_host(None)


def test_the_printed_url_survives_a_query_string_and_a_userinfo_prefix():
    assert safe_url("http://hooks.acme.io/github?token=abc123") == "http://hooks.acme.io/github"
    assert safe_url("https://bot:hunter2@hooks.acme.io/x") == "https://<redacted>@hooks.acme.io/x"
    assert "hunter2" not in safe_url("https://bot:hunter2@hooks.acme.io/x")
    assert safe_url("") == ""


def test_the_host_is_parsed_out_of_the_shapes_a_url_arrives_in():
    assert host_of("http://hooks.acme.io:8080/github") == "hooks.acme.io"
    assert host_of("http://bot:pw@10.0.0.4/hooks") == "10.0.0.4"
    assert host_of("http://[::1]:3000/hooks") == "::1"
    assert scheme_of("HTTP://hooks.acme.io") == "http"
    assert scheme_of("hooks.acme.io") == ""


def test_a_hook_with_no_url_is_not_counted_either_way():
    state, _ = classify({"id": 6, "config": {}})
    assert state == "no-scheme"
    assert classify({"id": 7, "config": {"url": "ftp://x.example/h"}})[0] == "unknown-scheme"


def test_the_summary_separates_leaking_from_unreachable():
    stats = summarize([OPEN, LOCAL, TLS, UNVERIFIED])
    assert stats == {"total": 4, "plaintext": 1, "unreachable": 1,
                     "encrypted": 2, "unreadable": 0}
