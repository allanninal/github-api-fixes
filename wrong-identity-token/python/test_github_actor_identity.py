from github_actor_identity import (
    attributed, classify, couplings, human_signals, identity,
    looks_like_a_person_name, machine_shaped,
)

PERSON = {"login": "jdoe", "type": "User", "name": "Jane Doe", "bio": "SRE",
          "hireable": True, "followers": 137}
APP = {"login": "acme-deploy[bot]", "type": "Bot", "name": "acme-deploy"}
BARE = {"login": "wj4", "type": "User", "name": None, "followers": 0}


def test_a_profile_reduces_to_login_type_and_name():
    assert identity(PERSON) == {"login": "jdoe", "type": "User",
                                "name": "Jane Doe"}


def test_a_body_with_no_login_is_not_an_identity():
    assert identity(None) is None
    assert identity({"message": "Resource not accessible by integration"}) is None
    assert identity([]) is None


def test_a_personal_name_needs_two_capitalised_words():
    assert looks_like_a_person_name("Jane Doe")
    assert not looks_like_a_person_name("acme-deploy")
    assert not looks_like_a_person_name("Jane")
    assert not looks_like_a_person_name(None)


def test_machine_hints_are_matched_as_tokens_and_not_as_substrings():
    assert machine_shaped("acme-ci")
    assert machine_shaped("deploy_bot")
    assert machine_shaped("acme-deploy[bot]")
    assert not machine_shaped("cindy")
    assert not machine_shaped("abbotsford")


def test_a_declared_machine_login_beats_the_heuristic():
    assert machine_shaped("hermes", declared=["hermes"])
    assert not machine_shaped("hermes")


def test_human_signals_are_named_individually():
    signals = human_signals(PERSON)
    assert any("personal name" in s for s in signals)
    assert any("bio" in s for s in signals)
    assert any("hireable" in s for s in signals)
    assert any("137 followers" in s for s in signals)


def test_an_email_is_counted_and_never_quoted():
    signals = human_signals({"login": "x", "email": "jane@acme.example"})
    assert signals == ["a public email address is set"]


def test_a_quiet_profile_produces_no_signals():
    assert human_signals(BARE) == []
    assert human_signals(None) == []


def test_a_bot_identity_is_the_healthy_answer():
    state, detail = classify(identity(APP), [], True)
    assert state == "app-installation"
    assert "employment" in detail
    assert couplings(state) == []


def test_a_person_behind_the_automation_is_the_finding():
    state, detail = classify(identity(PERSON), human_signals(PERSON), False)
    assert state == "personal-account"
    assert "running as a person" in detail
    assert any("deprovisioning" in c for c in couplings(state))


def test_a_machine_login_with_a_human_profile_is_its_own_state():
    body = dict(PERSON, login="acme-ci")
    state, detail = classify(identity(body), human_signals(body), True)
    assert state == "mixed-signals"
    assert "renamed" in detail


def test_a_clean_machine_account_is_a_compromise_rather_than_a_pass():
    state, detail = classify(identity({"login": "acme-ci", "type": "User"}),
                             [], True)
    assert state == "machine-account"
    assert "still an account with a seat" in detail


def test_an_unreadable_identity_is_reported_as_the_healthy_case():
    state, detail = classify(None, [], False)
    assert state == "identity-unreadable"
    assert "no user behind it" in detail


def test_a_bare_user_account_is_not_guessed_at():
    state, detail = classify(identity(BARE), [], False)
    assert state == "unclassified-user"
    assert "will not guess" in detail


def test_attribution_separates_mine_theirs_and_unlinked():
    commits = [
        {"author": {"login": "jdoe"}},
        {"author": {"login": "JDOE"}},
        {"author": {"login": "someone"}},
        {"author": None},
        {},
    ]
    assert attributed(commits, "jdoe") == {"total": 5, "attributed": 2,
                                           "unlinked": 2}
    assert attributed(None, "jdoe") == {"total": 0, "attributed": 0,
                                        "unlinked": 0}
