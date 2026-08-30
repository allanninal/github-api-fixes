from github_app_coverage_audit import coverage, expected_total


def test_org_total_needs_both_halves():
    assert expected_total({"public_repos": 40, "total_private_repos": 100}) == 140
    assert expected_total({"public_repos": 0, "total_private_repos": 0}) == 0


def test_a_missing_private_count_yields_no_total_at_all():
    # Falling back to public_repos here is how an unmeasurable gap becomes a
    # reassuring percentage.
    assert expected_total({"public_repos": 40}) is None
    assert expected_total({}) is None
    assert expected_total(None) is None


def test_all_repositories_is_the_only_good_news():
    state, detail = coverage("all", 140, 140)
    assert state == "all-repositories"
    assert "automatically" in detail


def test_twelve_of_a_hundred_and_forty_names_the_gap_and_the_share():
    state, detail = coverage("selected", 12, 140)
    assert state == "partial"
    assert "12 of 140" in detail
    assert "128" in detail
    assert "9%" in detail


def test_selected_and_complete_is_not_the_same_as_all():
    # Correct today, and nothing keeps it correct.
    state, detail = coverage("selected", 140, 140)
    assert state == "selected-complete"
    assert "coincidence" in detail


def test_no_org_total_means_a_count_not_a_coverage_figure():
    state, detail = coverage("selected", 12, None)
    assert state == "unmeasured"
    assert "not a coverage figure" in detail


def test_seeing_more_than_exists_is_reported_rather_than_averaged_away():
    state, _ = coverage("selected", 150, 140)
    assert state == "inconsistent"


def test_an_uninterpretable_selection_is_never_assumed_complete():
    assert coverage(None, 12, 140)[0] == "unknown-selection"
    assert coverage("some-new-value", 12, 140)[0] == "unknown-selection"
