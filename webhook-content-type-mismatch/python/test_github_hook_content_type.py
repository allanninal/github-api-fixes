from github_hook_content_type import (
    content_type_of, content_type_was_explicit, delivery_encoding,
    encoding_of_header, header_of, is_form_wrapped, parse_failures, receiver_of,
    repair, verdict, wrapper_evidence,
)

FORM_DELIVERY = {
    "id": 1,
    "status_code": 200,
    "request": {
        "headers": {"Content-Type": "application/x-www-form-urlencoded",
                    "X-GitHub-Event": "push"},
        "payload": {"payload": '{ "action": "opened" }'},
    },
}
JSON_DELIVERY = {
    "id": 2,
    "status_code": 200,
    "request": {
        "headers": {"content-type": "application/json; charset=utf-8"},
        "payload": {"action": "opened", "number": 7},
    },
}


def test_an_absent_content_type_is_form_not_unknown():
    assert content_type_of({}) == "form"
    assert content_type_of({"url": "https://example.com"}) == "form"
    assert not content_type_was_explicit({})
    assert content_type_was_explicit({"content_type": "form"})


def test_both_spellings_of_each_encoding_are_understood():
    assert content_type_of({"content_type": "json"}) == "json"
    assert content_type_of({"content_type": "application/json"}) == "json"
    assert content_type_of({"content_type": " FORM "}) == "form"
    assert content_type_of({"content_type": "application/x-www-form-urlencoded"}) == "form"
    assert content_type_of({"content_type": "text/xml"}) == "unknown"
    assert content_type_of(None) == "unknown"


def test_headers_are_read_case_insensitively_and_parameters_ignored():
    assert header_of({"Content-Type": "application/json"}, "content-type") == "application/json"
    assert header_of({"CONTENT-TYPE": "x"}, "Content-Type") == "x"
    assert header_of({}, "content-type") is None
    assert header_of(None, "content-type") is None
    assert encoding_of_header("application/json; charset=utf-8") == "json"
    assert encoding_of_header("application/x-www-form-urlencoded") == "form"
    assert encoding_of_header(None) == "unknown"


def test_the_delivery_record_is_read_from_the_request_half():
    assert delivery_encoding(FORM_DELIVERY) == "form"
    assert delivery_encoding(JSON_DELIVERY) == "json"
    assert delivery_encoding({"status_code": 200}) == "unknown"
    assert delivery_encoding(None) == "unknown"


def test_the_wrapper_is_one_string_key_and_nothing_else():
    assert is_form_wrapped({"payload": "{}"})
    assert not is_form_wrapped({"payload": {"action": "opened"}})
    assert not is_form_wrapped({"payload": "{}", "extra": 1})
    assert not is_form_wrapped({"action": "opened"})
    assert not is_form_wrapped(None)


def test_evidence_counts_the_header_and_the_body_separately():
    ev = wrapper_evidence([FORM_DELIVERY, JSON_DELIVERY, None])
    assert ev == {"sampled": 2, "form_header": 1, "form_wrapper": 1}


def test_parse_statuses_are_counted_but_only_the_three():
    hits, total = parse_failures([{"status_code": 400}, {"status_code": 415},
                                  {"status_code": 500}, {"status_code": 200},
                                  {"status_code": None}])
    assert (hits, total) == (2, 5)


def test_a_form_hook_against_a_json_receiver_is_the_finding():
    state, detail = verdict("form", "json")
    assert state == "form-to-json"
    assert "payload= field" in detail


def test_a_clean_delivery_log_does_not_soften_the_finding():
    ev = {"sampled": 5, "form_header": 5, "form_wrapper": 5}
    state, detail = verdict("form", "json", ev, failures=0, sampled_total=40)
    assert state == "form-to-json"
    assert "5 of 5 sampled deliveries" in detail


def test_parse_statuses_are_reported_as_corroboration():
    state, detail = verdict("form", "json", None, failures=12, sampled_total=40)
    assert state == "form-to-json"
    assert "12 of 40 recent attempts" in detail


def test_the_mirror_case_is_named_rather_than_folded_in():
    assert verdict("json", "form")[0] == "json-to-form"
    assert "wrong direction" in repair("json-to-form")


def test_an_undeclared_receiver_gives_a_risk_and_not_a_verdict():
    state, detail = verdict("form", None)
    assert state == "receiver-undeclared"
    assert "risk rather than a finding" in detail


def test_consistent_pairs_are_not_findings():
    assert verdict("json", "json")[0] == "consistent-json"
    assert verdict("form", "form")[0] == "consistent-form"


def test_a_consistent_form_hook_is_still_warned_about_the_signature():
    assert "raw bytes" in verdict("form", "form")[1]


def test_an_unrecognised_encoding_is_never_guessed_at():
    state, _ = verdict("unknown", "json")
    assert state == "encoding-unknown"
    assert "by hand" in repair("encoding-unknown")


def test_the_receiver_flag_is_normalised_defensively():
    assert receiver_of("JSON") == "json"
    assert receiver_of(" form ") == "form"
    assert receiver_of("maybe") == "unknown"
    assert receiver_of(None) == "unknown"


def test_the_repair_always_pairs_the_encoding_with_the_verifier():
    assert "raw request bytes" in repair("form-to-json")
    assert repair("consistent-json") == "nothing."
