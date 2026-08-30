from github_app_limit_ceiling import (
    classify_ceiling, entitled, is_lower_bound, reachable, repair,
    selection_of, shortfall, sustainable_repos, verdict,
)

WIDE = {"total_count": 400, "repository_selection": "all"}
NARROW = {"total_count": 9, "repository_selection": "selected"}


def test_nothing_scales_below_twenty_of_either_kind():
    assert entitled(0, 0) == 5000
    assert entitled(20, 20) == 5000
    assert entitled(19, 19) == 5000


def test_repositories_and_users_both_add():
    assert entitled(21, 0) == 5050
    assert entitled(0, 21) == 5050
    assert entitled(21, 21) == 5100


def test_the_cap_binds_outside_enterprise_cloud():
    assert entitled(1000, 1000) == 12500
    assert entitled(400, None) == 12500


def test_enterprise_replaces_the_sum_rather_than_extending_it():
    assert entitled(0, 0, enterprise=True) == 15000
    assert entitled(5000, 5000, enterprise=True) == 15000


def test_an_unknown_user_count_makes_the_answer_a_floor():
    assert entitled(30, None) == entitled(30, 0)
    assert entitled(30, 40) > entitled(30, None)
    assert is_lower_bound(None)
    assert not is_lower_bound(0)


def test_each_ceiling_has_a_name():
    assert classify_ceiling(60) == "unauthenticated"
    assert classify_ceiling(5000) == "baseline"
    assert classify_ceiling(7200) == "scaled"
    assert classify_ceiling(12500) == "at-cap"
    assert classify_ceiling(15000) == "enterprise"
    assert classify_ceiling(None) == "unknown"


def test_the_installation_view_is_read_defensively():
    assert selection_of(NARROW) == "selected"
    assert selection_of({"repository_selection": "ALL "}) == "all"
    assert selection_of({}) == "unknown"
    assert selection_of(None) == "unknown"
    assert reachable(WIDE) == 400
    assert reachable({"total_count": None}) is None
    assert reachable(None) is None


def test_a_small_installation_at_five_thousand_is_honest():
    state, detail = verdict(5000, "all", 9)
    assert state == "baseline"
    assert "repair is on the usage side" in detail


def test_a_narrow_installation_on_a_big_account_is_the_finding():
    state, detail = verdict(5000, "selected", 9, account_repos=400)
    assert state == "narrow-installation"
    assert "12500" in detail
    assert "selection is what is capping it" in detail


def test_a_scaled_ceiling_that_matches_its_size_is_not_a_finding():
    assert verdict(entitled(60, None), "all", 60)[0] == "scaled"


def test_the_cap_and_enterprise_are_never_reported_as_shortfalls():
    assert verdict(12500, "selected", 900, account_repos=4000)[0] == "at-cap"
    assert verdict(15000, "all", 4000)[0] == "enterprise"


def test_an_anonymous_ceiling_is_not_an_installation_problem():
    state, _ = verdict(60, "unknown", None, installation_seen=False)
    assert state == "unauthenticated"


def test_a_credential_with_no_installation_view_is_named_as_such():
    state, detail = verdict(5000, "unknown", None, installation_seen=False)
    assert state == "not-an-installation"
    assert "user or Actions credential" in detail


def test_the_shortfall_never_goes_negative():
    assert shortfall(12500, 5000) == 0
    assert shortfall(5000, 12500) == 7500
    assert shortfall(None, 12500) == 0


def test_the_budget_divides_the_ceiling_by_the_loop():
    assert sustainable_repos(12500, 10) == 1250
    assert sustainable_repos(5000, 12) == 416
    assert sustainable_repos(5000, 0) is None


def test_the_repair_for_a_real_ceiling_does_not_suggest_widening():
    assert "widen" not in repair("baseline")
    assert "widen the installation" in repair("narrow-installation")
