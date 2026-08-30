from github_api_version_pin import (
    behind, classify, confirms_version_refusal, is_version, nearest, supported,
)

SERVED = ["2022-11-28", "2024-06-10", "2025-04-01"]


def test_a_version_is_a_real_date_and_not_just_a_date_shape():
    assert is_version("2022-11-28")
    assert not is_version("2022-11-38")
    assert not is_version("2022-13-01")
    assert not is_version("latest")
    assert not is_version(None)


def test_the_versions_body_is_sorted_and_junk_is_dropped():
    assert supported(["2024-06-10", "2022-11-28", "latest", ""]) == [
        "2022-11-28", "2024-06-10"]
    assert supported(None) == []
    assert supported({"versions": []}) == []


def test_being_behind_is_counted_as_the_notes_still_to_read():
    assert behind("2022-11-28", SERVED) == ["2024-06-10", "2025-04-01"]
    assert behind("2025-04-01", SERVED) == []


def test_the_nearest_served_version_is_offered_for_a_typo():
    assert nearest("2024-06-01", SERVED) == "2024-06-10"
    assert nearest("2022-11-38", SERVED) == "2022-11-28"
    assert nearest("2022-11-28", []) is None


def test_the_current_pin_is_the_quiet_state():
    state, detail = classify("2025-04-01", SERVED)
    assert state == "supported-current"
    assert "newest version" in detail


def test_a_supported_but_behind_pin_is_the_one_to_alert_on():
    state, detail = classify("2022-11-28", SERVED)
    assert state == "supported-behind"
    assert "2 newer version(s)" in detail
    assert "notice attached" in detail


def test_a_retired_pin_is_named_as_older_than_everything_served():
    state, detail = classify("2021-04-01", SERVED)
    assert state == "retired"
    assert "2022-11-28" in detail


def test_a_date_that_was_never_a_version_is_its_own_state():
    state, detail = classify("2024-06-11", SERVED)
    assert state == "unknown-version"
    assert "2024-06-10" in detail


def test_a_future_date_is_a_typo_rather_than_a_retirement():
    assert classify("2099-01-01", SERVED)[0] == "not-yet-supported"


def test_a_value_that_is_not_a_date_is_a_typo_and_says_so():
    state, detail = classify("2022-11-38", SERVED)
    assert state == "malformed-pin"
    assert "never valid" in detail


def test_sending_no_header_is_a_state_rather_than_a_pass():
    state, detail = classify(None, SERVED)
    assert state == "unpinned"
    assert "pinned by the server" in detail
    assert classify("", SERVED)[0] == "unpinned"


def test_an_unpinned_client_is_warned_when_the_known_default_is_gone():
    _, detail = classify(None, ["2025-04-01"])
    assert "not on the served list" in detail


def test_an_empty_versions_list_is_a_failure_of_the_check_not_a_finding():
    state, detail = classify("2022-11-28", [])
    assert state == "no-versions-list"
    assert "failure of the check" in detail


def test_a_refusal_is_matched_on_words_and_not_on_a_status_code():
    assert confirms_version_refusal(410, "The API version is no longer supported")
    assert confirms_version_refusal(400, "X-GitHub-Api-Version is not supported")
    assert not confirms_version_refusal(200, "The API version is no longer supported")
    assert not confirms_version_refusal(403, "Resource not accessible by integration")
    assert not confirms_version_refusal(None, None)
