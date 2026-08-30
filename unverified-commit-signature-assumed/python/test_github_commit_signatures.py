from github_commit_signatures import (
    FAMILIES, REASONS, VIOLATIONS, author_allowlist_pass, disagreements,
    enforcement_from_rules, family_of, grade, identity_split, read_cost,
    repair, signature_pass, tally, verification_of,
)


def commit(sha, reason=None, verified=None, present=True, signature="-----BEGIN-----",
           author="alice@example.com", committer=None, linked=True):
    inner = {
        "author": {"email": author, "name": "Alice"},
        "committer": {"email": committer or author, "name": "Alice"},
    }
    if present:
        inner["verification"] = {"verified": verified, "reason": reason,
                                 "signature": signature, "payload": "tree 1",
                                 "verified_at": "2026-01-01T00:00:00Z"}
    return {"sha": sha, "commit": inner,
            "author": {"login": "alice"} if linked else None,
            "committer": {"login": "alice"} if linked else None}


SIGNED = commit("aaa", "valid", True)
UNSIGNED = commit("bbb", "unsigned", False, signature=None)
BAD = commit("ccc", "invalid", False)
UNREGISTERED = commit("ddd", "unknown_key", False)
OUTAGE = commit("eee", "gpgverify_unavailable", False)
ABSENT = commit("fff", present=False)


def test_every_documented_reason_has_a_family_and_a_sentence():
    for reason, (family, detail) in REASONS.items():
        assert family in FAMILIES, reason
        assert detail.endswith("."), reason
    assert REASONS["valid"][0] == "verified"


def test_the_four_kinds_of_false_are_four_different_findings():
    assert family_of(verification_of(UNSIGNED))[0] == "unsigned"
    assert family_of(verification_of(BAD))[0] == "signature-rejected"
    assert family_of(verification_of(UNREGISTERED))[0] == "identity-not-linked"
    assert family_of(verification_of(OUTAGE))[0] == "github-could-not-check"
    # And the one that matters most: a good signature nobody registered is not
    # a bad signature, and the sentence says so.
    assert "cryptography is fine" in family_of(verification_of(UNREGISTERED))[1]


def test_a_missing_verification_object_is_unknown_and_not_false():
    normalised = verification_of(ABSENT)
    assert normalised["present"] is False
    assert normalised["verified"] is None
    family, detail = family_of(normalised)
    assert family == "verification-absent"
    assert "not unsigned" in detail
    assert signature_pass(ABSENT) is None


def test_an_outage_is_unknown_rather_than_a_violation():
    assert signature_pass(OUTAGE) is None
    assert "github-could-not-check" not in VIOLATIONS
    state, detail = grade(tally([SIGNED, OUTAGE]), "no-rule")
    assert state == "checker-unavailable"
    assert "not a violation" in detail


def test_a_reason_github_adds_later_is_reported_not_defaulted():
    future = commit("ggg", "quantum_key_rotated", False)
    family, detail = family_of(verification_of(future))
    assert family == "unknown-reason"
    assert "rather than letting it fall into a default" in detail


def test_verified_true_beside_the_wrong_reason_is_not_believed():
    weird = commit("hhh", "unsigned", True)
    assert family_of(verification_of(weird))[0] == "unknown-reason"
    inverted = commit("iii", "valid", False)
    assert family_of(verification_of(inverted))[0] == "unknown-reason"


def test_the_author_check_and_the_signature_check_disagree_in_both_directions():
    allowed = ["alice@example.com"]
    # Author on the list, unsigned: the gap the policy has been missing.
    missed = disagreements([UNSIGNED], allowed)
    assert missed[0]["gap"] == "author-passed-signature-did-not"
    # Signed by somebody not on the roster: falsely flagged by the old check.
    outsider = commit("jjj", "valid", True, author="carol@example.com")
    flagged = disagreements([outsider], allowed)
    assert flagged[0]["gap"] == "signature-passed-author-did-not"
    # And where the two agree there is nothing to report.
    assert disagreements([SIGNED], allowed) == []


def test_the_author_check_authenticates_nothing():
    # The whole point: an unsigned commit claiming an approved author sails
    # through the check most people wrote.
    forged = commit("kkk", "unsigned", False, signature=None,
                    author="alice@example.com")
    assert author_allowlist_pass(forged, ["alice@example.com"]) is True
    assert signature_pass(forged) is False
    assert author_allowlist_pass(forged, []) is None


def test_the_signature_speaks_for_the_committer_not_the_author():
    split = commit("lll", "valid", True, author="alice@example.com",
                   committer="bob@example.com")
    state, detail = identity_split(split)
    assert state == "author-differs-from-committer"
    assert "speaks for the committer" in detail
    assert identity_split(SIGNED)[0] == "author-is-committer"
    unlinked = commit("mmm", "valid", True, linked=False)
    assert identity_split(unlinked)[0] == "email-resolves-to-no-account"


def test_an_unreadable_rule_is_not_an_absent_rule():
    state, detail = enforcement_from_rules(None, readable=False)
    assert state == "rule-unreadable"
    assert "not the same as unenforced" in detail
    assert enforcement_from_rules([], readable=True)[0] == "no-rule"
    rules = [{"type": "deletion"}, {"type": "required_signatures"}]
    assert enforcement_from_rules(rules, readable=True)[0] == "enforced"


def test_a_verified_history_with_no_rule_is_not_a_guarantee():
    counts = tally([SIGNED, SIGNED])
    assert grade(counts, "no-rule")[0] == "verified-but-not-enforced"
    assert grade(counts, "enforced")[0] == "verified-and-enforced"
    assert "not a constraint" in grade(counts, "no-rule")[1]


def test_absent_verification_outranks_every_other_grade():
    # A run that could not see the field is not a run that found violations.
    counts = tally([UNSIGNED, ABSENT])
    assert grade(counts, "enforced")[0] == "verification-unknown"


def test_the_tally_covers_every_family():
    counts = tally([SIGNED, UNSIGNED, BAD, UNREGISTERED, OUTAGE, ABSENT])
    assert set(counts) >= set(FAMILIES)
    assert counts["verified"] == 1 and counts["unsigned"] == 1
    assert counts["verification-absent"] == 1
    assert tally([]) == {name: 0 for name in FAMILIES}


def test_the_repair_asks_a_human_and_writes_nothing():
    fix = repair("identity-not-linked-present", "no-rule", "acme/payments", "main")
    assert "add their public keys" in fix
    assert "ask an admin of acme/payments" in fix
    assert fix.endswith("Nothing here writes.")
    assert "unreadable is not unenforced" in repair(
        "verified-and-enforced", "rule-unreadable", "acme/payments", "main")


def test_the_read_cost_is_known_before_anything_is_spent():
    assert read_cost(1, False) == 1
    assert read_cost(3, True) == 4
    assert read_cost(0, False) == 1
