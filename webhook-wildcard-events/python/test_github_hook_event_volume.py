from github_hook_event_volume import (
    handled_set, is_wildcard, never_seen, next_link, normalize,
    proposed_events, repair, subscribed, tally, verdict, waste,
)

HANDLES = "issues,pull_request,release"
STAR = {"id": 1, "events": ["*"], "config": {"url": "https://hooks.example.com/gh"}}
TIGHT = {"id": 2, "events": ["Issues", " pull_request "], "config": {}}


def test_event_names_are_normalised_narrowly():
    assert normalize(" Pull_Request ") == "pull_request"
    assert normalize(None) == ""
    assert subscribed(TIGHT) == ["issues", "pull_request"]
    assert subscribed({"events": "issues"}) == []
    assert subscribed(None) == []


def test_the_wildcard_is_recognised_however_it_is_written():
    assert is_wildcard(["*"])
    assert is_wildcard(["push", " * "])
    assert not is_wildcard(["push"])
    assert not is_wildcard([])


def test_the_handled_set_drops_a_wildcard_it_is_given():
    assert handled_set("issues, *, push") == {"issues", "push"}
    assert handled_set(["Issues", "issues"]) == {"issues"}
    assert handled_set("") == set()


def test_the_tally_counts_by_event_and_names_the_unknown():
    rows = [{"event": "push"}, {"event": "Push"}, {"event": None}, "junk"]
    assert tally(rows) == {"push": 2, "unknown": 1}


def test_the_waste_is_the_fraction_the_receiver_discards():
    counts = {"push": 300, "status": 110, "issues": 90}
    w = waste(counts, HANDLES)
    assert w["total"] == 500
    assert w["unhandled_deliveries"] == 410
    assert w["share"] == 82.0
    assert w["unhandled_events"] == ["push", "status"]


def test_an_empty_window_does_not_divide_by_zero():
    assert waste({}, HANDLES) == {"total": 0, "unhandled_deliveries": 0,
                                 "unhandled_events": [], "share": 0.0}


def test_a_wildcard_with_wasted_volume_is_the_headline_finding():
    counts = {"push": 300, "status": 110, "issues": 90}
    state, detail = verdict(subscribed(STAR), counts, HANDLES)
    assert state == "wildcard"
    assert "82.0%" in detail
    assert "ships next" in detail


def test_a_wildcard_stays_a_finding_when_the_window_was_all_wanted():
    state, detail = verdict(["*"], {"issues": 12}, HANDLES)
    assert state == "wildcard-all-handled"
    assert "luck rather than design" in detail


def test_a_wildcard_with_no_deliveries_is_still_reported():
    state, detail = verdict(["*"], {}, HANDLES)
    assert state == "wildcard-unmeasured"
    assert "open ended" in detail


def test_events_the_receiver_handles_and_the_hook_omits_are_not_this_finding():
    # release is handled and not subscribed; that is the other note's problem.
    state, _ = verdict(["issues", "pull_request"], {"issues": 4}, HANDLES)
    assert state == "tight"


def test_events_on_the_hook_and_not_in_the_code_are_this_finding():
    state, detail = verdict(["issues", "push", "status"], {"push": 3}, HANDLES)
    assert state == "over-subscribed"
    assert "push, status" in detail


def test_an_empty_subscription_is_its_own_state():
    assert verdict([], {}, HANDLES)[0] == "no-events"


def test_the_proposal_keeps_a_handled_event_that_never_fired():
    assert proposed_events(HANDLES) == ["issues", "pull_request", "release"]
    assert never_seen({"issues": 3}, HANDLES) == ["pull_request", "release"]
    text = repair("wildcard", HANDLES, {"issues": 3})
    assert '["issues", "pull_request", "release"]' in text
    assert "Keep pull_request, release on the list" in text


def test_a_tight_hook_gets_no_repair():
    assert repair("tight", HANDLES).startswith("nothing")


def test_the_cursor_is_read_from_the_link_header():
    header = '<https://api.github.com/repos/a/b/hooks/1/deliveries?cursor=v2>; rel="next"'
    assert next_link({"Link": header}).endswith("cursor=v2")
    assert next_link({}) is None
