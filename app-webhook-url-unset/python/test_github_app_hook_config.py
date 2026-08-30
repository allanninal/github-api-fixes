from datetime import datetime, timezone

from github_app_hook_config import (
    content_type_of, delivery_state, host_of, last_delivery, repair,
    secret_state, subscribed_events, url_class, verdict,
)

NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)
EVENTS = ["push", "pull_request", "issues", "release"]
RECENT = [{"delivered_at": "2026-08-30T10:00:00Z"}, {"delivered_at": "2026-08-29T10:00:00Z"}]
OLD = [{"delivered_at": "2026-01-04T10:00:00Z"}]


def test_a_host_is_pulled_out_of_a_url_or_admitted_missing():
    assert host_of("https://Hooks.Example.COM/github") == "hooks.example.com"
    assert host_of("nonsense") == ""
    assert host_of(None) == ""


def test_the_four_ways_this_actually_happens_are_each_named():
    assert url_class("") == "unset"
    assert url_class(None) == "unset"
    assert url_class("https://smee.io/aB3xQ9pLm") == "tunnel"
    assert url_class("https://1a2b3c.ngrok-free.app/hook") == "tunnel"
    assert url_class("https://example.com/webhook") == "placeholder"
    assert url_class("https://localhost:3000/hook") == "loopback"


def test_a_real_destination_is_not_swept_up_by_the_placeholder_list():
    assert url_class("https://hooks.acme.dev/github") == "production"
    assert url_class("https://api.example-corp.com/github") == "production"


def test_loopback_beats_transport_so_the_reader_goes_to_the_right_note():
    assert url_class("http://localhost:3000/hook") == "loopback"
    assert url_class("http://hooks.acme.dev/github") == "insecure"
    assert url_class("ftp://hooks.acme.dev/github") == "malformed"
    assert url_class("just-a-string") == "malformed"


def test_the_config_is_read_without_touching_the_secret():
    assert secret_state({"secret": "********"}) == "set"
    assert secret_state({"url": "https://x.dev"}) == "absent"
    assert content_type_of({}) == "form"
    assert content_type_of({"content_type": "JSON"}) == "json"
    assert subscribed_events({"events": EVENTS}) == EVENTS
    assert subscribed_events({}) == []


def test_a_blank_url_with_subscriptions_is_the_sharpest_form():
    state, detail = verdict("", EVENTS, "none")
    assert state == "no-url-subscribed"
    assert "4 event(s)" in detail
    assert "no log to read" in detail


def test_a_blank_url_with_no_subscriptions_is_reported_not_judged():
    state, detail = verdict("", [], "none")
    assert state == "no-url"
    assert "reported rather than judged" in detail


def test_a_tunnel_url_is_as_broken_as_a_blank_one_and_harder_to_see():
    state, detail = verdict("https://smee.io/aB3xQ9pLm", EVENTS, "recent")
    assert state == "tunnel-url"
    assert "nobody is listening" in detail


def test_the_delivery_log_is_read_and_never_trusted_alone():
    assert delivery_state([], NOW) == "none"
    assert delivery_state(RECENT, NOW) == "recent"
    assert delivery_state(OLD, NOW) == "stale"
    assert delivery_state(None, NOW) == "unknown"
    assert last_delivery(RECENT).day == 30
    assert last_delivery([]) is None


def test_an_empty_log_on_a_real_url_is_a_question_and_not_a_verdict():
    state, detail = verdict("https://hooks.acme.dev/github", EVENTS, "none")
    assert state == "no-deliveries"
    assert "genuinely not happened" in detail
    assert "not proof of anything" in repair("no-deliveries")


def test_a_real_url_with_no_subscriptions_is_handed_to_the_other_note():
    state, detail = verdict("https://hooks.acme.dev/github", [], "none")
    assert state == "no-events"
    assert "subscription finding" in detail


def test_a_working_app_is_not_a_finding():
    assert verdict("https://hooks.acme.dev/github", EVENTS, "recent")[0] == "delivering"
    assert verdict("https://hooks.acme.dev/github", EVENTS, "stale")[0] == "silent"


def test_the_repair_ends_at_the_delivery_log_rather_than_the_settings_page():
    assert "app/hook/deliveries" in repair("tunnel-url")
    assert "settings page" in repair("no-url-subscribed")
    assert "https" in repair("insecure-url")
