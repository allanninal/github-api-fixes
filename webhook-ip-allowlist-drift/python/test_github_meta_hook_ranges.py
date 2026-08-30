from github_meta_hook_ranges import (
    allows_everything, array_score, audit, best_other_array, coverage,
    covered_addresses, merge, overlap, parse_cidr, read_allowlist, repair,
    size_of, uncovered, verdict,
)

META = {
    "hooks": ["192.30.252.0/22", "140.82.112.0/20", "2a0a:a440::/29"],
    "api": ["10.10.0.0/16", "10.20.0.0/16"],
    "web": ["10.30.0.0/16"],
}


def parsed(*entries):
    return [parse_cidr(e) for e in entries]


def test_a_cidr_is_parsed_into_a_range_of_addresses():
    version, start, end = parse_cidr("192.30.252.0/22")
    assert version == 4
    assert end - start + 1 == 1024


def test_host_bits_and_bare_addresses_are_tolerated():
    assert parse_cidr("192.30.252.7/22") == parse_cidr("192.30.252.0/22")
    assert size_of(parse_cidr("140.82.112.5")) == 1
    assert parse_cidr("not-an-address") is None
    assert parse_cidr("   ") is None
    assert parse_cidr("# a comment") is None


def test_ipv6_ranges_are_understood():
    version, start, end = parse_cidr("2a0a:a440::/29")
    assert version == 6
    assert end > start


def test_the_two_families_never_cover_each_other():
    assert overlap(parse_cidr("0.0.0.0/0"), parse_cidr("2a0a:a440::/29")) is None
    assert coverage(parse_cidr("2a0a:a440::/29"), parsed("0.0.0.0/0")) == ("none", 0.0)


def test_a_subset_is_partial_with_the_fraction_it_permits():
    state, fraction = coverage(parse_cidr("192.30.252.0/22"), parsed("192.30.252.0/24"))
    assert state == "partial"
    assert round(fraction, 4) == 0.25


def test_a_superset_is_full_coverage_not_a_mismatch():
    assert coverage(parse_cidr("140.82.112.0/20"), parsed("140.82.0.0/16")) == ("full", 1.0)


def test_overlapping_rules_are_never_counted_twice():
    published = parse_cidr("192.30.252.0/22")
    allowed = parsed("192.30.252.0/24", "192.30.252.0/23")
    assert covered_addresses(published, allowed) == 512
    assert coverage(published, allowed)[0] == "partial"


def test_adjacent_rules_add_up_to_full_coverage():
    published = parse_cidr("192.30.252.0/23")
    allowed = parsed("192.30.252.0/24", "192.30.253.0/24")
    assert coverage(published, allowed) == ("full", 1.0)
    assert len(merge([overlap(published, a) for a in allowed])) == 1


def test_a_default_route_is_recognised_in_both_families():
    assert allows_everything(parsed("0.0.0.0/0"))
    assert allows_everything(parsed("::/0"))
    assert not allows_everything(parsed("10.0.0.0/8"))


def test_unreadable_allowlist_lines_are_returned_not_swallowed():
    ranges, unreadable = read_allowlist([
        "192.30.252.0/22", "  # comment", "", "140.82.112.0/20 # inline",
        "hooks.github.com",
    ])
    assert len(ranges) == 2
    assert unreadable == ["hooks.github.com"]


def test_the_audit_names_every_published_range():
    rows = audit(META["hooks"], parsed("140.82.112.0/20"))
    assert [state for _, state, _ in rows] == ["none", "full", "none"]
    assert uncovered(rows) == ["192.30.252.0/22", "2a0a:a440::/29"]


def test_drift_is_the_finding_when_some_ranges_are_short():
    allowed = parsed("192.30.252.0/24", "140.82.112.0/20", "2a0a:a440::/29")
    state, detail = verdict(META, allowed)
    assert state == "drifted"
    assert "1 of 3" in detail
    assert "intermittently" in detail


def test_a_list_built_from_the_wrong_array_is_named_as_such():
    allowed = parsed("10.10.0.0/16", "10.20.0.0/16")
    state, detail = verdict(META, allowed)
    assert state == "wrong-array"
    assert "api" in detail
    assert round(array_score(META, allowed, "api"), 4) == 1.0
    assert best_other_array(META, allowed)[0] == "api"


def test_a_default_route_passes_the_arithmetic_and_still_fails_the_audit():
    state, detail = verdict(META, parsed("0.0.0.0/0", "::/0"))
    assert state == "allow-all"
    assert "not filtering" in detail
    assert "never was" in repair("allow-all")


def test_a_complete_allowlist_is_current():
    allowed = parsed("192.30.252.0/22", "140.82.112.0/20", "2a0a:a440::/29")
    assert verdict(META, allowed)[0] == "current"


def test_unparsed_entries_downgrade_a_clean_result():
    allowed = parsed("192.30.252.0/22", "140.82.112.0/20", "2a0a:a440::/29")
    state, detail = verdict(META, allowed, unreadable=2)
    assert state == "current-with-gaps"
    assert "2 allow-list entries" in detail


def test_an_empty_allowlist_is_reported_rather_than_scored():
    assert verdict(META, [])[0] == "no-allowlist"
    assert verdict({}, parsed("10.0.0.0/8"))[0] == "no-hooks-array"


def test_the_repair_for_drift_is_automation_and_not_a_fresh_paste():
    assert "on a schedule" in repair("drifted")
    assert "hooks array" in repair("wrong-array")
