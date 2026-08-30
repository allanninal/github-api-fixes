from github_hook_delivery_duration import (
    by_event, classify, duration_ms, next_link, percentile, repair,
    slowest_event, stats, timed_out, verdict,
)


def d(duration, event="push", status="OK"):
    return {"duration": duration, "event": event, "status": status}


def test_seconds_and_milliseconds_both_normalise_to_milliseconds():
    assert duration_ms(d(0.62)) == 620.0
    assert duration_ms(d(9.87)) == 9870.0
    assert duration_ms(d(9870)) == 9870.0
    assert duration_ms(d(60)) == 60000.0
    assert duration_ms(d(61)) == 61.0


def test_an_unreadable_duration_is_none_rather_than_zero():
    assert duration_ms(d(None)) is None
    assert duration_ms(d("slow")) is None
    assert duration_ms(d(True)) is None
    assert duration_ms(d(-1)) is None
    assert duration_ms(None) is None


def test_an_abandoned_delivery_is_recognised_from_either_column():
    assert timed_out({"status": "timed out", "duration": None})
    assert timed_out({"status": "Timed Out", "duration": 2.0})
    assert timed_out({"status": "", "duration": 10.0})
    assert not timed_out(d(1.0))
    assert not timed_out(None)


def test_each_delivery_is_sorted_by_the_room_it_had_left():
    assert classify(d(9.5)) == "at-risk"
    assert classify(d(6.0)) == "slow"
    assert classify(d(0.4)) == "fine"
    assert classify(d(10.0)) == "timed-out"
    assert classify(d(None)) == "unknown"


def test_the_percentile_is_nearest_rank_and_never_invents_a_value():
    values = [100, 200, 300, 400]
    assert percentile(values, 50) == 200
    assert percentile(values, 95) == 400
    assert percentile(values, 0) == 100
    assert percentile([], 95) is None
    assert percentile([7], 95) == 7


def test_a_window_with_no_failures_and_a_nine_second_tail_is_a_finding():
    rows = [d(0.5)] * 18 + [d(9.1)] * 2
    st = stats(rows)
    assert st["timed_out"] == 0
    state, detail = verdict(st)
    assert state == "at-the-edge"
    assert "fails on the next slow week" in detail
    assert "return 202" in repair(state)


def test_a_fast_receiver_is_left_alone():
    st = stats([d(0.2)] * 50)
    assert verdict(st)[0] == "healthy"
    assert repair("healthy").startswith("nothing")


def test_timeouts_are_reported_with_the_headroom_on_everything_else():
    rows = [d(0.5)] * 90 + [{"status": "timed out", "event": "push"}] * 10
    st = stats(rows)
    assert st["timed_out"] == 10
    state, detail = verdict(st)
    assert state == "timing-out"
    assert "10 deliveries were abandoned" in detail


def test_an_empty_window_is_never_reported_as_healthy():
    state, detail = verdict(stats([]))
    assert state == "no-data"
    assert "not the same as a receiver that is fast" in detail


def test_a_window_with_statuses_but_no_timings_says_so():
    state, _ = verdict(stats([{"event": "push", "status": "OK"}] * 5))
    assert state == "no-durations"


def test_the_grouping_finds_the_handler_to_fix_first():
    rows = ([d(9.4, "push")] * 5 + [d(0.3, "issues")] * 5)
    worst = slowest_event(rows)
    assert worst["event"] == "push"
    assert worst["p95"] == 9400.0
    assert "Start with push" in repair("slow", worst)


def test_a_rare_event_is_kept_when_it_timed_out():
    rows = [d(0.2, "issues")] * 5 + [{"event": "release", "status": "timed out"}]
    grouped = by_event(rows)
    assert "release" in grouped
    assert grouped["release"]["timed_out"] == 1


def test_the_cursor_is_read_from_the_link_header():
    header = ('<https://api.github.com/repos/a/b/hooks/1/deliveries?cursor=v2>; '
              'rel="next"')
    assert next_link({"Link": header}).endswith("cursor=v2")
    assert next_link({"Link": '<https://x>; rel="prev"'}) is None
    assert next_link({}) is None
