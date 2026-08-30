from github_timeout_502 import (
    CUTOFF_SECONDS, GATEWAY, classify, is_gateway, is_throttled, lower_headers,
    narrow, narrowing_exhausted, near_cutoff, parse_params, read_cost, repair,
    request_id, retry_repeats_it, wasted_retries,
)

THROTTLE = {"Retry-After": "60"}
EXHAUSTED = {"X-RateLimit-Remaining": "0"}
RID = {"X-GitHub-Request-Id": "C4E2:1F03:9AB"}


def test_only_gateway_shaped_statuses_count():
    assert is_gateway(502)
    assert is_gateway(504)
    assert 500 not in GATEWAY
    assert not is_gateway(500)
    assert not is_gateway(200)
    assert not is_gateway(None)


def test_headers_are_read_case_insensitively():
    assert lower_headers(RID)["x-github-request-id"] == "C4E2:1F03:9AB"
    assert request_id(RID) == "C4E2:1F03:9AB"
    assert request_id({}) is None
    assert request_id(None) is None


def test_a_throttle_is_recognised_before_anything_else():
    assert is_throttled(403, THROTTLE)
    assert is_throttled(429, EXHAUSTED)
    assert not is_throttled(403, {})
    assert not is_throttled(502, THROTTLE)
    assert classify(403, 0.4, THROTTLE)[0] == "throttled"
    assert classify(429, 0.2, EXHAUSTED)[0] == "throttled"


def test_the_cutoff_has_a_tolerance_and_it_is_generous():
    assert near_cutoff(10.4)
    assert near_cutoff(8.0)
    assert not near_cutoff(7.9)
    assert not near_cutoff(0.3)
    assert not near_cutoff(None)


def test_a_gateway_error_at_the_cutoff_is_the_finding():
    state, detail = classify(502, 10.4, RID)
    assert state == "timeout"
    assert "10.4s" in detail
    assert "too expensive" in detail


def test_the_same_status_arriving_fast_is_a_different_diagnosis():
    state, detail = classify(502, 0.3, {})
    assert state == "gateway-early"
    assert "status page" in detail


def test_a_success_just_under_the_line_is_not_a_pass():
    state, detail = classify(200, 9.4, {})
    assert state == "slow-success"
    assert "fails on the week" in detail
    assert classify(200, 0.4, {})[0] == "ok"


def test_the_other_failures_are_named_rather_than_lumped_in():
    assert classify(500, 3.0, {})[0] == "server-other"
    assert classify(404, 0.2, {})[0] == "client-error"
    assert classify(None, 30.0, {})[0] == "client-timeout"
    assert classify(None, None, {})[0] == "unknown"
    assert classify("not a status", 1.0, {})[0] == "unknown"


def test_only_the_states_a_retry_cannot_fix_are_called_repeatable():
    assert retry_repeats_it("timeout")
    assert retry_repeats_it("client-timeout")
    assert not retry_repeats_it("gateway-early")
    assert not retry_repeats_it("throttled")
    assert wasted_retries("timeout", 3) == 3
    assert wasted_retries("gateway-early", 3) == 0
    assert wasted_retries("timeout", None) == 0


def test_narrowing_halves_the_page_and_keeps_everything_else():
    assert narrow({"per_page": 100})["per_page"] == 50
    assert narrow({})["per_page"] == 50
    assert narrow({"per_page": 1})["per_page"] == 1
    assert narrow({"per_page": 40, "since": "2026-01-01"})["since"] == "2026-01-01"
    assert not narrowing_exhausted({"per_page": 2})
    assert narrowing_exhausted({"per_page": 1})
    assert not narrowing_exhausted({})


def test_the_repair_for_a_timeout_never_says_retry():
    text = repair("timeout", {"per_page": 100})
    assert "cheaper" in text
    assert "x-github-request-id" in text
    assert "split by range" in repair("timeout", {"per_page": 1})
    assert "wait exactly as long" in repair("throttled")
    assert "status page" in repair("gateway-early")
    assert repair("ok") == "nothing."


def test_the_baseline_is_never_counted_as_spending():
    assert read_cost(["/a"], 2) == 2
    assert read_cost(["/a", "/b"], 3) == 6
    assert read_cost(["/a"], 0) == 0
    assert read_cost([], 2) == 0
    assert read_cost(None, 2) == 0


def test_parameters_survive_a_value_containing_an_equals_sign():
    assert parse_params(["per_page=100", "q=repo:acme/x is:open"]) == {
        "per_page": "100", "q": "repo:acme/x is:open"}
    assert parse_params(["base=v1...main"])["base"] == "v1...main"
    assert parse_params(None) == {}
