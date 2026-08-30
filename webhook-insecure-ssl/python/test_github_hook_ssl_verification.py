from datetime import datetime, timezone

from github_hook_ssl_verification import (
    classify, endpoint, has_secret, insecure_flag, repair, scheme_of,
    summarize, unchanged_days,
)

NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)

OPEN = {"id": 1, "updated_at": "2025-09-23T08:00:00Z",
        "config": {"url": "https://hooks.acme.io/github", "insecure_ssl": "1",
                   "secret": "********", "content_type": "json"}}
SAFE = {"id": 2, "updated_at": "2026-08-01T08:00:00Z",
        "config": {"url": "https://hooks.acme.io/github", "insecure_ssl": "0",
                   "secret": "********", "content_type": "json"}}
PLAIN = {"id": 3, "updated_at": "2026-08-01T08:00:00Z",
         "config": {"url": "http://hooks.acme.io/github", "insecure_ssl": "0"}}


def test_the_string_zero_is_not_a_finding():
    assert insecure_flag(SAFE) == "off"
    assert insecure_flag({"config": {"insecure_ssl": 0}}) == "off"
    assert insecure_flag({"config": {"insecure_ssl": False}}) == "off"
    assert classify(SAFE, NOW)[0] == "verified"


def test_every_spelling_of_on_is_on():
    assert insecure_flag(OPEN) == "on"
    assert insecure_flag({"config": {"insecure_ssl": 1}}) == "on"
    assert insecure_flag({"config": {"insecure_ssl": True}}) == "on"
    assert insecure_flag({"config": {"insecure_ssl": "true"}}) == "on"


def test_an_absent_flag_is_unknown_rather_than_either_answer():
    assert insecure_flag({"config": {"url": "https://x.example"}}) == "unknown"
    assert insecure_flag({"config": {"insecure_ssl": "maybe"}}) == "unknown"
    assert insecure_flag({"id": 4}) == "unknown"
    state, detail = classify({"id": 4, "config": {"url": "https://x.example"}}, NOW)
    assert state == "flag-unreadable"
    assert "rather than assuming" in detail


def test_a_plaintext_hook_is_handed_to_the_other_question():
    state, detail = classify(PLAIN, NOW)
    assert state == "not-applicable"
    assert "the scheme is" in detail
    assert "behind HTTPS" in repair(state, PLAIN)


def test_the_finding_names_the_endpoint_and_dates_it_as_a_lower_bound():
    state, detail = classify(OPEN, NOW)
    assert state == "verification-off"
    assert "https://hooks.acme.io/github" in detail
    assert "at least 341 day(s)" in detail
    assert unchanged_days(OPEN, NOW) == 341


def test_a_hook_with_no_url_is_its_own_state():
    state, _ = classify({"id": 5, "config": {"insecure_ssl": "1"}}, NOW)
    assert state == "no-url"


def test_the_printed_url_drops_any_query_string():
    hook = {"id": 6, "config": {"url": "https://hooks.acme.io/github?token=abc123"}}
    assert endpoint(hook) == "https://hooks.acme.io/github"
    assert scheme_of(hook) == "https"
    assert scheme_of({"config": {"url": "not-a-url"}}) == ""


def test_the_repair_is_a_whole_config_not_one_field():
    text = repair("verification-off", OPEN)
    assert "full" in text
    assert "replaced, not merged" in text
    assert "new secret" in text


def test_a_hook_with_no_secret_gets_told_to_set_one():
    hookless = {"id": 7, "config": {"url": "https://x.example", "insecure_ssl": "1"}}
    assert not has_secret(hookless)
    assert "since this hook has none" in repair("verification-off", hookless)


def test_the_summary_keeps_the_plaintext_hooks_out_of_the_finding_count():
    stats = summarize([OPEN, SAFE, PLAIN], NOW)
    assert stats == {"total": 3, "verification_off": 1, "verified": 1,
                     "plaintext": 1, "unreadable": 0}


def test_an_unparseable_timestamp_produces_no_age():
    assert unchanged_days({"updated_at": "whenever"}, NOW) is None
    assert unchanged_days({"id": 1}, NOW) is None
