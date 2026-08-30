from github_token_dormancy import (
    dormancy_state, keepalive_cron, margin_days, probe_interval, reap_exposure,
    token_class,
)


def test_each_prefix_names_its_class():
    assert token_class("ghp_fake") == "classic"
    assert token_class("github_pat_fk") == "fine-grained"
    assert token_class("ghs_fake") == "installation"
    assert token_class("gho_fake") == "oauth"
    assert token_class("ghu_fake") == "oauth"
    assert token_class("0" * 40) == "classic"
    assert token_class("something") == "unknown"
    assert token_class(None) == "absent"


def test_an_expiry_header_takes_a_credential_out_of_scope():
    state, detail = reap_exposure("classic", "2026-09-30 12:00:00 UTC")
    assert state == "not-reapable-expiring"
    assert "different check" in detail


def test_a_classic_token_with_no_expiry_is_the_reapable_class():
    assert reap_exposure("classic", None)[0] == "reapable"


def test_the_other_classes_die_of_other_causes():
    assert reap_exposure("fine-grained", None)[0] == "not-reapable-fine-grained"
    assert reap_exposure("installation", None)[0] == "not-reapable-short-lived"
    assert reap_exposure("oauth", None)[0] == "not-reapable-oauth"
    assert reap_exposure("unknown", None)[0] == "unknown-class"


def test_margin_is_the_window_minus_the_cadence():
    assert margin_days(1) == 364
    assert margin_days(90) == 275
    assert margin_days(365) == 0


def test_an_unknown_cadence_is_not_guessed_at():
    assert margin_days(None) is None
    assert margin_days("sometimes") is None


def test_an_annual_job_has_lost_the_race_before_it_starts():
    state, detail = dormancy_state(200, "reapable", 365)
    assert state == "reap-race-lost"
    assert "before it is next needed" in detail


def test_one_day_inside_the_window_is_still_tight():
    assert dormancy_state(200, "reapable", 364)[0] == "reap-race-tight"


def test_a_frequent_job_keeps_its_own_credential_alive():
    assert dormancy_state(200, "reapable", 1)[0] == "covered"
    assert dormancy_state(200, "reapable", 90)[0] == "covered"


def test_a_reaped_credential_is_already_gone():
    state, detail = dormancy_state(401, "reapable", 1)
    assert state == "already-gone"
    assert "nothing to un-revoke" in detail


def test_a_credential_out_of_scope_is_reported_as_such():
    assert dormancy_state(200, "not-reapable-expiring", 365)[0] == "not-reapable"


def test_an_unknown_class_is_treated_as_reapable():
    assert dormancy_state(200, "unknown-class", 365)[0] == "reap-race-lost"


def test_a_missing_cadence_is_its_own_state():
    state, detail = dormancy_state(200, "reapable", None)
    assert state == "cadence-unknown"
    assert "how often" in detail


def test_a_broken_probe_says_nothing_about_the_credential():
    assert dormancy_state(500, "reapable", 1)[0] == "unreachable"
    assert dormancy_state(None, "reapable", 1)[0] == "unreachable"


def test_the_probe_is_never_slower_than_a_month():
    assert probe_interval(365) == 30
    assert probe_interval(90) == 30
    assert probe_interval(7) == 7
    assert probe_interval(1) == 1


def test_an_unknown_cadence_gets_the_monthly_probe():
    assert probe_interval(None) == 30


def test_the_cadence_becomes_a_crontab_line():
    assert keepalive_cron(1) == "0 6 * * *"
    assert keepalive_cron(7) == "0 6 * * 1"
    assert keepalive_cron(30) == "0 6 1 * *"
