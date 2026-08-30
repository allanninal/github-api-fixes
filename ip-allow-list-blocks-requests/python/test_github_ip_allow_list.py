from github_ip_allow_list import (
    ALLOW_LIST_QUERY, address_in_message, allow_list_from_graphql,
    cidr_contains, classify_refusal, covered_by, egress_assumption,
    ipv4_to_int, list_that_applies, looks_like_ipv4, looks_like_ipv6,
    paired_reading, read_cost, refuses_mutation, repair, token_kind, verdict,
)

ALLOW_LIST_BODY = (
    "Although you appear to have the correct authorization credentials, the "
    "ACME organization has an IP allow list enabled, and 203.0.113.9 is not "
    "permitted to access this resource."
)
UA_BODY = ("Request forbidden by administrative rules. Please make sure your "
           "request has a User-Agent header.")
QUOTA_BODY = "API rate limit exceeded for user ID 12345."
SECONDARY_BODY = "You have exceeded a secondary rate limit."
PERMISSION_BODY = "Resource not accessible by integration"

ENTRIES = [
    {"value": "198.51.100.0/24", "active": True, "name": "office"},
    {"value": "203.0.113.0/24", "active": False, "name": "old ci"},
    {"value": "2001:db8::/32", "active": True, "name": "ipv6 office"},
]


def test_the_allow_list_refusal_is_the_only_one_naming_an_address():
    state, detail = classify_refusal(403, ALLOW_LIST_BODY, {})
    assert state == "ip-allow-list"
    assert "names an IP address" in detail
    assert classify_refusal(403, UA_BODY, {})[0] == "user-agent-rule"
    assert classify_refusal(403, PERMISSION_BODY, {})[0] == "permission-or-role"


def test_quota_and_secondary_limits_are_sorted_out_first():
    assert classify_refusal(403, QUOTA_BODY, {})[0] == "primary-quota-exhausted"
    assert classify_refusal(429, SECONDARY_BODY, {})[0] == "secondary-limit"
    # The header is enough on its own, because a proxy can replace the body.
    assert classify_refusal(403, "", {"X-RateLimit-Remaining": "0"})[0] == (
        "primary-quota-exhausted")


def test_a_reworded_allow_list_message_still_classifies():
    # The English is corroboration. The address is the signal, so GitHub can
    # rewrite the sentence without this becoming a permission problem.
    reworded = "Access from 198.51.100.77 is blocked by policy for this org."
    assert classify_refusal(403, reworded, {})[0] == "ip-allow-list"


def test_an_allow_list_message_with_no_address_is_kept_apart():
    state, _ = classify_refusal(403, "This org has an IP allow list enabled.", {})
    assert state == "ip-allow-list-unaddressed"


def test_a_200_is_not_a_refusal_to_sort():
    assert classify_refusal(200, "[]", {})[0] == "not-a-refusal"
    assert classify_refusal(401, "Bad credentials", {})[0] == "credential-rejected"


def test_the_address_survives_the_full_stop_at_the_end_of_the_sentence():
    assert address_in_message(ALLOW_LIST_BODY) == "203.0.113.9"
    assert address_in_message("from (2001:db8::1) today") == "2001:db8::1"
    assert address_in_message("no address at all here") is None
    # A version number is four groups but not four bytes.
    assert address_in_message("version 1.2.3.400 shipped") is None


def test_what_an_address_looks_like():
    assert looks_like_ipv4("203.0.113.9") is True
    assert looks_like_ipv4("203.0.113") is False
    assert looks_like_ipv4("203.0.113.256") is False
    assert looks_like_ipv6("2001:db8::1") is True
    assert looks_like_ipv6("203.0.113.9") is False


def test_cidr_arithmetic_at_the_edges():
    assert cidr_contains("203.0.113.0/24", "203.0.113.9") is True
    assert cidr_contains("203.0.113.0/24", "203.0.114.9") is False
    assert cidr_contains("203.0.113.9", "203.0.113.9") is True
    assert cidr_contains("0.0.0.0/0", "8.8.8.8") is True
    assert ipv4_to_int("0.0.0.1") == 1


def test_an_unevaluated_entry_is_none_and_not_false():
    # Reporting an IPv6 entry as a miss would tell somebody their address is
    # uncovered when the entry covering it was one this script cannot read.
    assert cidr_contains("2001:db8::/32", "203.0.113.9") is None
    assert cidr_contains("not-a-cidr", "203.0.113.9") is None
    assert cidr_contains("203.0.113.0/xx", "203.0.113.9") is None


def test_an_entry_that_exists_but_is_switched_off_is_its_own_finding():
    state, entry = covered_by(ENTRIES, "203.0.113.9")
    assert state == "covered-but-inactive"
    assert entry["name"] == "old ci"
    assert verdict("ip-allow-list", state, "ENABLED")[0] == "entry-exists-but-is-off"


def test_coverage_reports_the_entries_it_could_not_evaluate():
    state, entry = covered_by(ENTRIES, "192.0.2.5")
    assert state == "not-covered-some-unevaluated"
    assert entry is None
    assert covered_by(ENTRIES, "198.51.100.4")[0] == "covered"
    assert covered_by([], "198.51.100.4")[0] == "no-entries"


def test_a_wrong_egress_assumption_is_named_before_anybody_files_a_ticket():
    state, detail = egress_assumption(["198.51.100.0/24"], "203.0.113.9")
    assert state == "egress-assumption-wrong"
    assert "would not have helped" in detail
    assert egress_assumption(["203.0.113.0/24"], "203.0.113.9")[0] == (
        "egress-as-expected")
    assert egress_assumption([], "203.0.113.9")[0] == "nothing-declared"


def test_the_pair_of_readings_is_the_headline():
    state, detail = paired_reading(403, 200)
    assert state == "network-path"
    assert "source address" in detail
    assert paired_reading(403, 403)[0] == "refused-everywhere"
    assert paired_reading(403, None)[0] == "single-reading"
    assert paired_reading(200, 200)[0] == "no-refusal"


def test_an_installation_token_and_a_user_token_are_judged_differently():
    which, _ = list_that_applies("App installation token")
    assert which == "org-list-plus-app-managed"
    which, detail = list_that_applies("App user-to-server token")
    assert which == "org-list-only"
    assert "background sync works" in detail
    assert token_kind("ghs_x") == "App installation token"
    assert token_kind("ghu_x") == "App user-to-server token"


def test_an_unreadable_list_is_not_an_empty_one():
    body = {"data": {"organization": None},
            "errors": [{"message": "Resource not accessible"}]}
    setting, apps, entries, note = allow_list_from_graphql(body)
    assert setting is None and entries == []
    assert "admin:org" in note
    state, detail = verdict("ip-allow-list", "rule-unread", None)
    assert state == "rule-unreadable"
    assert "cause is established" in detail


def test_the_entries_are_normalised_off_the_graphql_shape():
    body = {"data": {"organization": {
        "ipAllowListEnabledSetting": "ENABLED",
        "ipAllowListForInstalledAppsEnabledSetting": "DISABLED",
        "ipAllowListEntries": {"nodes": [
            {"allowListValue": "198.51.100.0/24", "isActive": True, "name": "office"},
        ]}}}}
    setting, apps, entries, _ = allow_list_from_graphql(body)
    assert setting == "ENABLED" and apps == "DISABLED"
    assert entries == [{"value": "198.51.100.0/24", "active": True, "name": "office"}]


def test_the_query_this_script_sends_is_a_read():
    assert refuses_mutation(ALLOW_LIST_QUERY) is False
    assert refuses_mutation("mutation M { createIpAllowListEntry { id } }") is True
    assert refuses_mutation("subscription S { x }") is True


def test_the_repair_asks_a_human_and_adds_nothing():
    fix = repair("address-not-covered", "203.0.113.9", "acme")
    assert "ask an owner of acme" in fix
    assert "203.0.113.9/32" in fix
    assert "adds anything" in fix
    assert "switch the existing entry back on" in repair(
        "entry-exists-but-is-off", "203.0.113.9", "acme")


def test_the_two_budgets_are_counted_separately():
    assert read_cost() == (2, 0)
    assert read_cost(True) == (2, 1)
