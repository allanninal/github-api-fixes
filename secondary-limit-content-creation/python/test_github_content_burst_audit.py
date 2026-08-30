from github_content_burst_audit import by_actor, parse_ts, peak_rate, verdict

NOW = 1756512000.0  # 2025-08-30T00:00:00Z, so every case below is anchored.


def issue(login, created_at, kind="Bot"):
    return {"user": {"login": login, "type": kind}, "created_at": created_at}


def test_parse_ts_reads_githubs_z_suffix():
    assert parse_ts("2025-08-30T00:00:00Z") == NOW


def test_parse_ts_returns_none_rather_than_raising():
    assert parse_ts(None) is None
    assert parse_ts("") is None
    assert parse_ts("last tuesday") is None


def test_a_naive_timestamp_is_read_as_utc_not_as_local_time():
    # Two machines in two timezones must not disagree about the same log.
    assert parse_ts("2025-08-30T00:00:00") == NOW


def test_peak_rate_of_a_steady_trickle_is_one_per_window():
    times = [NOW + 120 * i for i in range(10)]
    peak, _ = peak_rate(times, 60)
    assert peak == 1


def test_peak_rate_finds_the_burst_and_says_when_it_ended():
    times = [NOW + i for i in range(90)] + [NOW + 10000]
    peak, at = peak_rate(times, 60)
    assert peak == 60
    assert at == NOW + 59


def test_the_window_edge_is_exclusive_so_a_full_minute_counts_once():
    assert peak_rate([NOW, NOW + 60], 60)[0] == 1
    assert peak_rate([NOW, NOW + 59.9], 60)[0] == 2


def test_peak_rate_of_nothing_is_zero():
    assert peak_rate([], 60) == (0, None)
    assert peak_rate(None, 60) == (0, None)


def test_by_actor_groups_per_login_and_keeps_the_account_type():
    grouped = by_actor([issue("bot", "2025-08-30T00:00:00Z"),
                        issue("bot", "2025-08-30T00:00:01Z"),
                        issue("person", "2025-08-30T00:00:02Z", "User")])
    assert sorted(grouped) == ["bot", "person"]
    assert len(grouped["bot"]["times"]) == 2
    assert grouped["person"]["type"] == "User"


def test_by_actor_drops_items_with_no_readable_timestamp():
    grouped = by_actor([issue("bot", None), issue("bot", "2025-08-30T00:00:00Z")])
    assert len(grouped["bot"]["times"]) == 1


def test_eighty_in_a_minute_is_the_finding():
    state, detail = verdict(80, 80, NOW, NOW)
    assert state == "over-minute"
    assert "still running" in detail


def test_a_burst_that_finished_hours_ago_is_reported_as_finished():
    state, detail = verdict(90, 90, NOW - 7200, NOW)
    assert state == "over-minute"
    assert "already finished" in detail
    assert "120 minute(s) ago" in detail


def test_a_gentle_rate_can_still_break_the_hourly_ceiling():
    # Ten a minute never trips the per-minute limit and is 600 an hour.
    state, detail = verdict(10, 600, NOW, NOW)
    assert state == "over-hour"
    assert "per-minute limit is not enough" in detail


def test_the_near_states_warn_before_the_ceiling():
    assert verdict(64, 64, NOW, NOW)[0] == "near-minute"
    assert verdict(10, 400, NOW, NOW)[0] == "near-hour"


def test_an_ordinary_repository_is_clear():
    state, _ = verdict(3, 40, NOW, NOW)
    assert state == "clear"


def test_no_activity_is_quiet_rather_than_clear():
    assert verdict(0, 0, None, NOW)[0] == "quiet"
