from github_hook_secret_audit import secret_state, unauthorized, verdict

SIGNED = {"id": 1, "config": {"url": "https://hooks.example.com/gh",
                              "secret": "********", "content_type": "json"}}
UNSIGNED = {"id": 2, "config": {"url": "https://hooks.example.com/gh",
                                "content_type": "json"}}


def test_a_missing_key_is_the_finding():
    # GitHub omits the key entirely rather than returning an empty string.
    assert secret_state(UNSIGNED) == "absent"


def test_a_masked_value_means_a_secret_exists():
    assert secret_state(SIGNED) == "set"


def test_an_empty_secret_counts_as_absent():
    assert secret_state({"config": {"secret": "  "}}) == "absent"


def test_a_hook_without_config_is_not_silently_signed():
    assert secret_state({"id": 3}) == "unknown"
    assert verdict({"id": 3})[0] == "unknown"


def test_the_unsigned_detail_names_the_missing_header():
    state, detail = verdict(UNSIGNED)
    assert state == "unsigned"
    assert "X-Hub-Signature-256" in detail
    assert "hooks.example.com" in detail


def test_signed_admits_it_cannot_check_the_value():
    state, detail = verdict(SIGNED)
    assert state == "signed"
    assert "masked" in detail
    assert "whether it matches" in detail


def test_a_run_of_refusals_on_a_signed_hook_is_its_own_state():
    state, detail = verdict(SIGNED, rejected=18, delivered=20)
    assert state == "rejected"
    assert "mismatched secret" in detail


def test_one_refusal_in_fifty_is_not_a_mismatch():
    state, detail = verdict(SIGNED, rejected=1, delivered=50)
    assert state == "signed"
    assert "1 of 50" in detail


def test_unauthorized_counts_only_auth_failures():
    rejected, total = unauthorized([{"status_code": 401}, {"status_code": 403},
                                    {"status_code": 500}, {"status_code": 200},
                                    {"status_code": None}])
    assert (rejected, total) == (2, 5)
