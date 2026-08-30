from github_hook_delivery_audit import bucket, summarize, triage, verdict


def delivery(code, status="failure", when="2026-08-01T10:00:00Z", did=1, redelivery=False):
    return {"id": did, "status": status, "status_code": code,
            "delivered_at": when, "redelivery": redelivery}


def test_two_hundred_is_the_only_success():
    assert bucket(delivery(200, "OK")) == "ok"
    assert bucket(delivery(204, "OK")) == "ok"


def test_no_status_code_is_unreachable_not_a_server_error():
    # Nothing answered, so there is no stack trace to go and read.
    assert bucket(delivery(0)) == "unreachable"
    assert bucket({"status": "failure"}) == "unreachable"


def test_a_timeout_is_its_own_bucket_whatever_the_code_says():
    assert bucket({"status": "timed out", "status_code": 0}) == "timeout"


def test_auth_failures_are_separated_from_other_client_errors():
    assert bucket(delivery(401)) == "rejected"
    assert bucket(delivery(403)) == "rejected"
    assert bucket(delivery(404)) == "client-error"
    assert bucket(delivery(502)) == "server-error"


def test_triage_treats_a_null_code_as_never_delivered():
    state, detail = triage({"last_response": {"code": None, "status": "unused"}})
    assert state == "never"
    assert "no delivery" in detail


def test_triage_reads_the_failing_code_and_message():
    state, detail = triage({"last_response": {"code": 502, "message": "Bad Gateway"}})
    assert state == "failing"
    assert "502" in detail and "Bad Gateway" in detail


def test_summarize_keeps_both_ends_of_the_window():
    s = summarize([
        delivery(200, "OK", "2026-08-01T10:00:00Z"),
        delivery(500, when="2026-08-02T10:00:00Z", did=2),
        delivery(500, when="2026-08-03T10:00:00Z", did=3, redelivery=True),
    ])
    assert s["total"] == 3 and s["ok"] == 1 and s["failed"] == 2
    assert s["first_failed"] == "2026-08-02T10:00:00Z"
    assert s["last_failed"] == "2026-08-03T10:00:00Z"
    assert s["last_ok"] == "2026-08-01T10:00:00Z"
    assert s["redeliveries"] == 1
    assert s["guids"]["server-error"] == [2, 3]


def test_an_empty_log_is_not_a_healthy_hook():
    state, _ = verdict(summarize([]))
    assert state == "empty"


def test_failures_older_than_the_last_success_are_already_fixed():
    s = summarize([delivery(500, when="2026-08-01T10:00:00Z"),
                   delivery(200, "OK", "2026-08-02T10:00:00Z", did=2)])
    state, detail = verdict(s)
    assert state == "recovered"
    assert "replay" in detail


def test_the_dominant_bucket_names_the_repair():
    s = summarize([delivery(500), delivery(500, did=2), delivery(404, did=3)])
    state, detail = verdict(s)
    assert state == "server-error"
    assert "handler" in detail


def test_a_run_of_401s_points_at_the_secret_without_claiming_to_read_it():
    s = summarize([delivery(401), delivery(401, did=2)])
    state, detail = verdict(s)
    assert state == "rejected"
    assert "will not compare secrets" in detail
