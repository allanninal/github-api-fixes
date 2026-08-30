from github_compare_truncation import verdict


def compare(total, received, files=0):
    return {"total_commits": total,
            "commits": [{"sha": "%040x" % i} for i in range(received)],
            "files": [{"filename": "f%d" % i} for i in range(files)]}


def test_a_small_comparison_is_complete():
    state, detail = verdict(compare(18, 18, files=42))
    assert state == "complete"
    assert "18 commit(s)" in detail
    assert "42 changed file(s)" in detail


def test_exactly_250_with_more_to_come_is_the_cap():
    state, detail = verdict(compare(812, 250))
    assert state == "capped"
    assert "562 commit(s) are missing" in detail
    # The sharp edge: the array is not a contiguous prefix of the history.
    assert "not the 250th commit" in detail


def test_a_partial_page_is_not_the_same_finding_as_the_cap():
    state, detail = verdict(compare(812, 100))
    assert state == "truncated"
    assert "712 commit(s) are missing" in detail


def test_no_commits_between_the_refs_is_not_a_failure():
    assert verdict(compare(0, 0))[0] == "empty"


def test_a_missing_total_commits_is_never_reported_as_complete():
    # Defaulting the count to zero here would call a truncated comparison
    # complete, which is precisely the bug being hunted.
    state, _ = verdict({"commits": [{"sha": "abc"}]})
    assert state == "unknown"
