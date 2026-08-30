from github_unstable_sort import (
    DEFAULT_DIRECTION, compare_walks, duplicates_within, evidence, normalize,
    parse_link, read_cost, repair, sort_kind, stable_params, verdict, walk_risk,
)


def test_sort_keys_are_sorted_into_movers_and_non_movers():
    assert sort_kind("updated") == "mutable"
    assert sort_kind("PUSHED") == "mutable"
    assert sort_kind("comments") == "mutable"
    assert sort_kind("created") == "immutable"
    assert sort_kind("full_name") == "immutable"
    assert sort_kind("banana") == "unknown"
    assert sort_kind(None) == "unknown"


def test_the_risk_has_three_outcomes_not_two():
    assert walk_risk("updated", "desc")[0] == "skips-and-duplicates"
    assert walk_risk("updated", "asc")[0] == "skips-and-duplicates"
    assert walk_risk("created", "desc")[0] == "duplicates-only"
    assert walk_risk("created", "asc")[0] == "append-only"
    assert walk_risk("banana", "asc")[0] == "unknown"
    assert walk_risk("created", "sideways")[0] == "unknown"


def test_a_missing_direction_is_treated_as_the_one_that_shifts():
    assert DEFAULT_DIRECTION == "desc"
    assert walk_risk("created")[0] == "duplicates-only"


def test_only_the_mutable_key_can_hide_a_record():
    assert "hidden" in walk_risk("created", "desc")[1]
    assert "neither skip" in walk_risk("created", "asc")[1]
    assert "only one of them is visible" in walk_risk("updated", "desc")[1]


def test_repeats_inside_one_walk_are_found_and_deduplicated():
    assert duplicates_within([1, 2, 2, 3, 3, 3]) == ["2", "3"]
    assert duplicates_within([1, 2, 3]) == []
    assert duplicates_within([]) == []
    assert duplicates_within(None) == []


def test_ids_are_compared_as_strings_so_two_walks_line_up():
    assert normalize([1, "1", 2]) == ["1", "1", "2"]
    diff = compare_walks([1, 2, 3], ["1", "2", "4"])
    assert diff["missing"] == ["3"]
    assert diff["appeared"] == ["4"]
    assert diff["first_count"] == 3


def test_growth_in_an_append_only_walk_is_not_a_finding():
    diff = compare_walks([1, 2, 3], [1, 2, 3, 4])
    assert evidence("append-only", diff) == []
    assert evidence("skips-and-duplicates", diff) == ["4"]


def test_a_shifting_window_proves_nothing_from_set_differences():
    diff = compare_walks([1, 2, 3], [0, 1, 2])
    assert evidence("duplicates-only", diff) == []
    assert evidence("append-only", diff) == ["3"]


def test_a_record_in_one_walk_and_not_the_other_is_the_finding():
    state, detail = verdict("updated", "desc", [1, 2, 3], [1, 2, 4])
    assert state == "proven-skips"
    assert "never returned" in detail


def test_a_repeat_inside_a_walk_is_reported_as_the_gentler_failure():
    state, detail = verdict("created", "desc", [1, 2, 2, 3], [1, 2, 3])
    assert state == "proven-duplicates"
    assert "Nothing was hidden" in detail


def test_agreeing_walks_on_a_mutable_sort_are_exposure_not_a_pass():
    state, detail = verdict("updated", "desc", [1, 2, 3], [1, 2, 3])
    assert state == "exposed"
    assert "quiet window rather than a safe walk" in detail


def test_the_safe_ordering_comes_back_clean():
    assert verdict("created", "asc", [1, 2, 3], [1, 2, 3])[0] == "stable-walk"
    assert verdict("created", "desc", [1, 2, 3], [1, 2, 3])[0] == "insertion-shift"
    assert verdict("banana", "asc", [1], [1])[0] == "unknown"


def test_a_walk_with_no_evidence_still_gets_classified():
    assert verdict("updated", "desc")[0] == "exposed"
    assert verdict("created", "asc")[0] == "stable-walk"


def test_the_repairs_are_different_for_skips_and_for_duplicates():
    assert "sort=created&direction=asc" in repair("proven-skips")
    assert "since=" in repair("exposed")
    assert "deduplicate on id" in repair("proven-duplicates")
    assert "Nothing is being lost" in repair("insertion-shift")
    assert repair("stable-walk").startswith("nothing on the ordering")


def test_the_printed_repair_is_a_request_you_can_send():
    assert stable_params() == {"sort": "created", "direction": "asc", "per_page": 100}
    assert stable_params(50, "2026-01-01T00:00:00Z")["since"] == "2026-01-01T00:00:00Z"


def test_the_walk_follows_the_header_rather_than_counting_pages():
    header = ('<https://api.github.com/repos/o/n/issues?labels=bug,ci&page=2>; rel="next", '
              '<https://api.github.com/repos/o/n/issues?labels=bug,ci&page=9>; rel="last"')
    assert set(parse_link(header)) == {"next", "last"}
    assert parse_link(None) == {}


def test_two_walks_cost_twice_what_one_does():
    assert read_cost(3) == 6
    assert read_cost(3, 1) == 3
    assert read_cost(0) == 0
    assert read_cost(None) == 0
