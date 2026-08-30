from github_hook_signature_headers import (
    format_hit, header_names, normalized, receiver_state, redacted_config,
    repair, scan_line, scan_source, secret_state, signature_headers, verdict,
)

MODERN_LINE = 'sig = request.headers["X-Hub-Signature-256"]'
LEGACY_LINE = 'sig = request.headers["X-Hub-Signature"]'
WSGI_LINE = 'sig = environ["HTTP_X_HUB_SIGNATURE_256"]'

SECRET_SET = {"id": 1, "config": {"url": "https://example.com/hook",
                                  "secret": "********", "content_type": "json"}}
NO_SECRET = {"id": 2, "config": {"url": "https://example.com/hook",
                                 "content_type": "json"}}


def test_the_modern_header_is_not_read_as_a_legacy_one():
    assert scan_line(MODERN_LINE) == ["sha256"]
    assert scan_line(LEGACY_LINE) == ["sha1"]
    assert scan_line("nothing to see here") == []


def test_a_line_naming_both_headers_reports_both():
    line = 'const h = req.headers["x-hub-signature-256"] ?? req.headers["x-hub-signature"];'
    assert scan_line(line) == ["sha256", "sha1"]


def test_the_runtime_spellings_are_the_same_header():
    assert scan_line(WSGI_LINE) == ["sha256"]
    assert scan_line("X_HUB_SIGNATURE") == ["sha1"]
    assert normalized("X-Hub_Signature-256") == "x-hub-signature-256"


def test_the_scan_reports_line_numbers_and_never_lines():
    text = "\n".join(["import os", LEGACY_LINE, "", MODERN_LINE])
    hits = scan_source(text, "receiver/hooks.py")
    assert hits == [("receiver/hooks.py", 2, "sha1"),
                    ("receiver/hooks.py", 4, "sha256")]
    rendered = [format_hit(h) for h in hits]
    assert rendered[0] == "receiver/hooks.py:2 legacy X-Hub-Signature"
    assert rendered[1] == "receiver/hooks.py:4 modern X-Hub-Signature-256"
    assert not any("request.headers" in line for line in rendered)


def test_the_receiver_state_separates_only_legacy_from_both():
    assert receiver_state([("a", 1, "sha1")]) == "sha1-only"
    assert receiver_state([("a", 1, "sha256")]) == "sha256-only"
    assert receiver_state([("a", 1, "sha256"), ("a", 1, "sha1")]) == "both"
    assert receiver_state([]) == "none"


def test_a_masked_secret_is_presence_and_never_a_value():
    assert secret_state(SECRET_SET) == "set"
    assert secret_state(NO_SECRET) == "absent"
    assert secret_state({"id": 3}) == "unknown"
    safe = redacted_config(SECRET_SET["config"])
    assert safe["secret"] == "<set>"
    assert "********" not in str(safe)


def test_header_names_are_matched_exactly_and_values_dropped():
    sent = {"X-Hub-Signature": "sha1=deadbeef",
            "X-Hub-Signature-256": "sha256=deadbeef",
            "Content-Type": "application/json"}
    assert signature_headers(sent) == {"sha256": True, "sha1": True}
    assert signature_headers({"X-Hub-Signature-256": "x"}) == {"sha256": True, "sha1": False}
    assert signature_headers({}) == {"sha256": False, "sha1": False}
    assert "deadbeef" not in str(header_names(sent))


def test_delivery_headers_arrive_in_more_than_one_shape():
    as_list = [{"name": "X-Hub-Signature-256", "value": "sha256=x"},
               {"name": "Content-Type", "value": "application/json"}]
    assert signature_headers(as_list)["sha256"] is True
    assert signature_headers(["X-Hub-Signature: sha1=x"])["sha1"] is True
    assert signature_headers(None) == {"sha256": False, "sha1": False}


def test_a_legacy_receiver_is_the_finding():
    state, detail = verdict("set", {"sha256": True, "sha1": True}, "sha1-only")
    assert state == "sha1-only"
    assert "being ignored" in detail
    assert "constant-time" in repair(state)


def test_accepting_both_headers_is_still_a_finding():
    state, detail = verdict("set", {"sha256": True, "sha1": True}, "both")
    assert state == "both-accepted"
    assert "weaker" in detail


def test_a_hook_with_no_secret_is_sent_to_a_different_note():
    state, detail = verdict("absent", None, "sha1-only")
    assert state == "no-secret"
    assert "different and larger problem" in detail


def test_with_no_source_the_script_declines_to_guess():
    state, detail = verdict("set", {"sha256": True, "sha1": True}, None)
    assert state == "not-scanned"
    assert "not visible from the API" in detail


def test_finding_nothing_is_not_reported_as_finding_a_problem():
    state, detail = verdict("set", {"sha256": True, "sha1": True}, "none")
    assert state == "no-verification-found"
    assert "at runtime" in detail


def test_a_correct_receiver_passes():
    state, _ = verdict("set", {"sha256": True, "sha1": True}, "sha256-only")
    assert state == "sha256-only"
    assert repair(state).startswith("nothing")
