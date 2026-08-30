from github_fork_or_upstream import (
    PUSH_DAYS_OBVIOUS, classify, days_between, divergence, fork_chain, is_fork,
    parse_ts, quiet_audit_reasons, read_cost, repair, upstream_of,
)

FORK = {
    "id": 904113,
    "node_id": "R_kgDONjA",
    "fork": True,
    "has_issues": False,
    "open_issues_count": 0,
    "forks_count": 0,
    "stargazers_count": 41,
    "pushed_at": "2025-01-14T09:22:10Z",
    "default_branch": "master",
    "parent": {"full_name": "octo-org/platform-core"},
    "source": {"full_name": "octo-org/platform-core"},
}
FORK_OF_FORK = dict(FORK, parent={"full_name": "acme/platform-core"},
                    source={"full_name": "octo-org/platform-core"})
UPSTREAM = {
    "id": 20221,
    "fork": False,
    "has_issues": True,
    "open_issues_count": 9134,
    "forks_count": 1875,
    "stargazers_count": 12400,
    "pushed_at": "2026-08-28T17:03:44Z",
    "default_branch": "main",
}


def test_a_fork_is_a_separate_repository_and_that_is_the_finding():
    assert is_fork(FORK) is True
    state, detail = classify(FORK)
    assert state == "fork-as-canonical"
    assert "own issues, releases and branches" in detail
    assert classify(UPSTREAM)[0] == "canonical"


def test_source_is_preferred_over_parent():
    # Repointing at parent when the two disagree moves the integration one hop
    # closer and leaves it reading a fork, which is the annoying half-fix.
    assert upstream_of(FORK_OF_FORK) == "octo-org/platform-core"
    assert fork_chain(FORK_OF_FORK)["parent"] == "acme/platform-core"
    state, detail = classify(FORK_OF_FORK)
    assert state == "fork-of-fork"
    assert "root of the network is octo-org/platform-core" in detail


def test_a_repository_with_no_upstream_reports_none():
    assert upstream_of(UPSTREAM) is None
    assert fork_chain({}) == {"parent": None, "source": None}


def test_id_drift_is_checked_before_the_fork_question():
    # This is the case where nobody changed anything: the name now resolves to
    # a different object, and fork=false is no help at all.
    state, detail = classify(dict(UPSTREAM, id=88), expected_id=20221)
    assert state == "id-drift"
    assert "20221" in detail and "88" in detail
    assert classify(UPSTREAM, expected_id=20221)[0] == "canonical"
    assert classify(UPSTREAM, expected_id="")[0] == "canonical"


def test_the_gap_is_reported_in_units_a_person_recognises():
    gaps = divergence(FORK, UPSTREAM)
    assert gaps["stargazers_count"] == {"fork": 41, "upstream": 12400,
                                        "difference": 12359}
    assert gaps["open_issues_count"]["difference"] == 9134
    assert gaps["default_branch"] == {"fork": "master", "upstream": "main"}
    assert gaps["obvious"] is True


def test_a_close_copy_is_not_reported_as_obvious():
    near = dict(FORK, stargazers_count=12000, pushed_at="2026-08-27T10:00:00Z")
    gaps = divergence(near, UPSTREAM)
    assert gaps["obvious"] is False
    assert gaps["pushed_days_behind"] < PUSH_DAYS_OBVIOUS


def test_timestamps_are_parsed_and_differenced():
    assert parse_ts("2026-08-28T17:03:44Z") is not None
    assert parse_ts("not a date") is None
    assert parse_ts(None) is None
    assert days_between("2026-08-01T00:00:00Z", "2026-08-28T00:00:00Z") == 27
    assert days_between("nope", "2026-08-28T00:00:00Z") is None


def test_the_quiet_symptoms_are_gathered_under_one_cause():
    reasons = quiet_audit_reasons(FORK, releases=0)
    joined = " ".join(reasons)
    assert "issues are disabled" in joined
    assert "no open issues" in joined
    assert "no releases" in joined
    assert "nothing has forked it" in joined
    assert quiet_audit_reasons(UPSTREAM, releases=3) == []


def test_disabled_issues_on_a_fork_answers_410_not_an_empty_list():
    # The symptom that sends people to the disabled-feature note instead of to
    # the fact that this was never the right repository.
    assert "410" in " ".join(quiet_audit_reasons(FORK))


def test_the_repair_names_the_upstream_and_the_id_to_store():
    fix = repair("fork-as-canonical", FORK)
    assert "octo-org/platform-core" in fix
    assert "store its id" in fix
    drift = repair("id-drift", dict(UPSTREAM, id=88), expected_id=20221)
    assert "88" in drift and "20221" in drift
    assert "survives a rename" in repair("canonical", UPSTREAM)


def test_the_run_costs_two_reads_by_default():
    assert read_cost() == 2
    assert read_cost(False) == 1
    assert read_cost(True, True) == 4
