from github_token_lifetime import (
    DEFAULT_FINE_GRAINED_MAX_DAYS, cap_verdict, days_between,
    expiry_absent_meaning, granted_lifetime_days, grants_over_cap,
    header_value, org_probe_verdict, parse_stamp, policy_applies, read_cost,
    repair, rotation_fit, token_kind, verdict,
)

# Obviously fake and far shorter than any real credential.
FINE = "github_pat_FAKE"
CLASSIC = "ghp_FAKE"
INSTALLATION = "ghs_FAKE"

NOW = 1_800_000_000.0
DAY = 86400.0


def test_the_documented_header_shape_parses_and_so_does_the_iso_one():
    assert parse_stamp("2026-09-30 12:00:00 UTC") == 1790769600.0
    assert parse_stamp("2026-09-30T12:00:00Z") == 1790769600.0
    assert parse_stamp("2026-09-30") == 1790726400.0
    assert parse_stamp("not a date") is None
    assert parse_stamp(None) is None
    # A shape it cannot read returns None rather than a plausible wrong date.
    assert parse_stamp("30/09/2026") is None


def test_the_header_is_read_case_insensitively():
    assert header_value({"Github-Authentication-Token-Expiration": "x"}) == "x"
    assert header_value({"unrelated": "y"}) is None
    assert header_value(None) is None


def test_the_granted_lifetime_needs_an_issue_date_and_says_so():
    granted = granted_lifetime_days(NOW - 30 * DAY, NOW + 60 * DAY)
    assert round(granted) == 90
    # Without an issue date the answer is unknown, not a guess.
    assert granted_lifetime_days(None, NOW + 60 * DAY) is None
    state, detail = cap_verdict(None, 90)
    assert state == "lifetime-unknown"
    assert "without an issue date" in detail


def test_a_token_over_the_cap_is_blocked_not_shortened():
    state, detail = cap_verdict(366, 90)
    assert state == "over-org-cap"
    assert "it does not shorten them" in detail
    assert cap_verdict(60, 90)[0] == "within-org-cap"


def test_an_undeclared_cap_is_not_an_absent_one():
    state, detail = cap_verdict(366, None)
    assert state == "cap-not-declared"
    assert "no documented endpoint" in detail


def test_the_schedule_that_can_never_work_is_kept_apart_from_the_one_off():
    # The recurring finding: the interval is longer than any lifetime allowed.
    state, detail = rotation_fit(90, 80, 365)
    assert state == "rotation-outlives-token"
    assert "once per cycle, forever" in detail
    # The one-off: this token dies first, and the schedule itself is fine.
    state, detail = rotation_fit(365, 20, 90)
    assert state == "this-cycle-expires-first"
    assert "A one-off" in detail
    assert rotation_fit(365, 300, 90)[0] == "fits"
    assert rotation_fit(365, -1, 90)[0] == "already-expired"
    assert rotation_fit(None, 300, None)[0] == "rotation-not-declared"


def test_the_two_findings_have_two_different_repairs():
    recurring = verdict("within-org-cap", "rotation-outlives-token", "policy-applies")
    assert recurring[0] == "schedule-cannot-work"
    assert "every cycle" in recurring[1]
    once = verdict("within-org-cap", "this-cycle-expires-first", "policy-applies")
    assert once[0] == "rotate-early-this-once"
    assert "Bring the rotation forward" in once[1]
    assert "GitHub App" in repair("schedule-cannot-work", 365, 90)
    assert "bring this rotation forward" in repair("rotate-early-this-once", 90, None)


def test_being_over_the_cap_outranks_the_schedule():
    # A token already over the cap is being refused now; the schedule is the
    # cause and the block is the symptom, so the block is reported first.
    state, _ = verdict("over-org-cap", "fits", "policy-applies")
    assert state == "blocked-by-lifetime-policy"


def test_the_policy_only_governs_one_credential_class():
    assert policy_applies("fine-grained PAT")[0] == "policy-applies"
    assert str(DEFAULT_FINE_GRAINED_MAX_DAYS) in policy_applies("fine-grained PAT")[1]
    state, detail = policy_applies("classic PAT")
    assert state == "different-class"
    assert "auto-revocation note" in detail
    assert policy_applies("App installation token")[0] == "minted-hourly"
    assert policy_applies("unknown")[0] == "class-unknown"
    assert token_kind(FINE) == "fine-grained PAT"
    assert token_kind(CLASSIC) == "classic PAT"
    assert token_kind(INSTALLATION) == "App installation token"
    assert token_kind("") == "unknown"


def test_a_wrong_class_ends_the_note_rather_than_grading_it():
    state, detail = verdict("cap-not-declared", "fits", "minted-hourly")
    assert state == "minted-hourly"
    assert "not about your problem" in detail
    assert "no action from this note" in repair("minted-hourly", None, None)


def test_the_missing_header_means_different_things_per_class():
    assert expiry_absent_meaning("classic PAT")[0] == "no-expiry-on-this-class"
    assert "larger exposure" in expiry_absent_meaning("classic PAT")[1]
    assert expiry_absent_meaning("App installation token")[0] == "short-lived-by-design"
    assert expiry_absent_meaning("fine-grained PAT")[0] == "expiry-not-reported"


def test_the_org_probe_reports_a_shape_and_names_its_rivals():
    state, detail = org_probe_verdict(200, 403)
    assert state == "refused-by-one-org"
    assert "Three things produce that shape" in detail
    assert "narrows the search rather than ending it" in detail
    assert org_probe_verdict(200, 200)[0] == "org-reachable"
    assert org_probe_verdict(401, 403)[0] == "credential-dead"
    assert org_probe_verdict(200, None)[0] == "org-not-probed"


def test_the_fleet_read_sorts_by_which_credential_goes_next():
    grants = [
        {"owner": {"login": "carol"}, "token_expires_at": "2026-12-01 00:00:00 UTC",
         "token_expired": False},
        {"owner": {"login": "alice"}, "token_expires_at": None, "token_expired": False},
        {"owner": {"login": "bob"}, "token_expires_at": "2026-06-01 00:00:00 UTC",
         "token_expired": False},
    ]
    rows = grants_over_cap(grants, 90, NOW)
    assert [r["owner"] for r in rows] == ["bob", "carol", "alice"]
    # A grant with no expiry at all cannot satisfy any maximum lifetime.
    assert rows[-1]["no_expiry"] is True
    assert rows[-1]["over_declared_cap"] is True
    assert grants_over_cap([], 90, NOW) == []


def test_the_free_probe_is_counted_as_free():
    assert read_cost(False, False) == (1, 0)
    assert read_cost(True, False) == (2, 1)
    assert read_cost(True, True) == (3, 2)


def test_days_between_is_signed_and_none_safe():
    assert days_between(NOW, NOW + DAY) == 1.0
    assert days_between(NOW, NOW - DAY) == -1.0
    assert days_between(None, NOW) is None
