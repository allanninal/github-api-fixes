from github_credential_differential import compare, diagnose, ladder, outcome, shape

DEAD = [("public", "unauthenticated"), ("identity", "unauthenticated"),
        ("repository", "unauthenticated")]
ALIVE = [("public", "ok"), ("identity", "ok"), ("repository", "ok")]


def test_the_ladder_only_includes_rungs_it_can_probe():
    assert ladder() == [("public", "/"), ("identity", "/user")]
    rungs = ladder(repo="acme/api", org="acme")
    assert rungs[2] == ("repository", "/repos/acme/api")
    assert rungs[3] == ("organization", "/orgs/acme")


def test_status_codes_reduce_to_what_they_say_about_a_credential():
    assert outcome(200) == "ok"
    assert outcome(401) == "unauthenticated"
    assert outcome(403) == "forbidden"
    assert outcome(404) == "missing"
    assert outcome(500) == "other"
    assert outcome(0) == "error"
    assert outcome(None) == "error"


def test_a_total_failure_and_a_partial_one_have_different_names():
    assert shape(DEAD) == "uniform-401"
    assert shape(ALIVE) == "healthy"
    assert shape([("public", "ok"), ("identity", "forbidden")]) == "selective"
    assert shape([("public", "missing"), ("identity", "forbidden")]) == "mixed"
    assert shape([]) == "nothing-probed"


def test_a_rung_the_control_never_ran_does_not_count_as_agreement():
    rows = compare(ALIVE, [("public", "ok")])
    assert rows[0]["agrees"] is True
    assert rows[1]["control"] is None
    assert rows[1]["agrees"] is False


def test_without_a_control_the_script_declines_to_name_a_cause():
    state, detail = diagnose(DEAD)
    assert state == "no-control"
    assert "expiry, revocation and a truncated string" in detail


def test_a_uniform_401_against_a_healthy_control_is_the_credential():
    state, detail = diagnose(DEAD, ALIVE)
    assert state == "credential-is-the-variable"
    assert "eliminated" in detail
    assert "expired" not in state


def test_two_dead_credentials_are_not_two_expiries():
    state, detail = diagnose(DEAD, DEAD)
    assert state == "both-dead"
    assert "same second" in detail


def test_identical_failures_on_one_rung_are_the_resource():
    suspect = [("public", "ok"), ("identity", "ok"), ("repository", "missing")]
    state, detail = diagnose(suspect, list(suspect))
    assert state == "resource-changed"
    assert "repository" in detail


def test_a_credential_that_authenticates_anything_has_not_expired():
    suspect = [("public", "ok"), ("identity", "ok"), ("repository", "forbidden")]
    state, detail = diagnose(suspect, ALIVE)
    assert state == "access-not-expiry"
    assert "has not expired" in detail
    assert "repository (forbidden)" in detail


def test_a_healthy_suspect_sends_you_somewhere_else():
    assert diagnose(ALIVE, ALIVE)[0] == "suspect-healthy"
    assert diagnose(ALIVE)[0] == "suspect-healthy"


def test_disagreeing_shapes_are_reported_rather_than_narrated():
    suspect = [("public", "missing"), ("identity", "forbidden")]
    control = [("public", "ok"), ("identity", "unauthenticated")]
    state, detail = diagnose(suspect, control)
    assert state == "mixed"
    assert "rather than picking a story" in detail


def test_nothing_probed_is_not_a_pass():
    assert diagnose([], ALIVE)[0] == "nothing-probed"
