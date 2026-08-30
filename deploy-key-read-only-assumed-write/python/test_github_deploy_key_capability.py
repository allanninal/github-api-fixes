import json

from github_deploy_key_capability import (
    DEFAULT_MAX_AGE_DAYS, SAFE_FIELDS, age_days, attribute_git_error,
    capability, read_cost, redact, redact_all, repair, stale_keys, verdict,
    writable_keys,
)

# Obviously not a key. The point of the fixture is that it never comes back out.
FAKE_MATERIAL = "ssh-ed25519 FAKE"

READ_ONLY = {"id": 41288114, "key": FAKE_MATERIAL, "title": "ci-fetch",
             "read_only": True, "created_at": "2021-06-02T11:03:00Z",
             "verified": True, "added_by": "dana-ops"}
WRITABLE = {"id": 55210987, "key": FAKE_MATERIAL, "title": "release-runner",
            "read_only": False, "created_at": "2024-11-18T08:00:00Z",
            "verified": True, "added_by": "build-bot"}


def test_the_key_material_never_leaves_the_script():
    reduced = redact(READ_ONLY)
    assert "key" not in reduced
    assert FAKE_MATERIAL not in json.dumps(reduced)
    assert FAKE_MATERIAL not in json.dumps(redact_all([READ_ONLY, WRITABLE]))
    assert set(reduced) <= set(SAFE_FIELDS)
    assert reduced["id"] == 41288114
    assert reduced["read_only"] is True


def test_redaction_survives_junk_without_leaking_it():
    assert redact(None) == {}
    assert redact("not a key") == {}
    assert redact_all(None) == []
    assert redact_all([None, "x", READ_ONLY]) == [redact(READ_ONLY)]


def test_the_capability_is_a_declared_field_not_an_experiment():
    assert capability(READ_ONLY) == "read-only"
    assert capability(WRITABLE) == "read-write"
    assert capability({"id": 1}) == "unknown"
    assert capability(None) == "unknown"
    assert writable_keys([READ_ONLY, WRITABLE]) == [55210987]
    assert writable_keys([READ_ONLY]) == []


def test_a_pushing_job_with_only_read_only_keys_is_the_finding():
    state, detail = verdict(200, [READ_ONLY, READ_ONLY], True)
    assert state == "write-needed-none-capable"
    assert "all 2 deploy key(s)" in detail
    assert "cannot be edited on an existing key" in repair(state)
    assert "contents: write" in repair(state)


def test_the_same_keys_are_correct_when_nothing_pushes():
    state, detail = verdict(200, [READ_ONLY], False)
    assert state == "read-only-and-correct"
    assert "recommended arrangement" in detail
    assert repair(state).startswith("nothing.")


def test_a_write_capable_key_is_reported_either_way():
    assert verdict(200, [READ_ONLY, WRITABLE], True)[0] == "write-capable-key-present"
    state, detail = verdict(200, [READ_ONLY, WRITABLE], False)
    assert state == "write-capable-but-unused"
    assert "standing grant" in detail


def test_a_refused_listing_is_not_an_empty_inventory():
    state, detail = verdict(403, [], True)
    assert state == "keys-unreadable"
    assert "not the same as the repository having no keys" in detail
    assert "Do not record the keys as absent" in repair(state)
    assert verdict(404, [], False)[0] == "keys-unreadable"
    assert verdict(None, [], False)[0] == "keys-unreadable"


def test_no_keys_at_all_is_its_own_answer():
    state, detail = verdict(200, [], True)
    assert state == "no-deploy-keys"
    assert "authenticating with something else" in detail
    assert "which credential your clone actually uses" in repair(state)


def test_the_read_only_message_names_the_key_itself():
    state, detail = attribute_git_error(
        "ERROR: The key you are authenticating with has been marked as read only.")
    assert state == "deploy-key-read-only"
    assert "not a scope, a token or SSH" in detail


def test_three_of_the_four_messages_send_you_somewhere_else():
    assert attribute_git_error(
        "remote: error: GH006: Protected branch update failed")[0] == (
        "refused-by-branch-protection")
    assert attribute_git_error(
        "remote: Repository was archived so is read-only.")[0] == "repository-archived"
    assert attribute_git_error(
        "git@github.com: Permission denied (publickey).")[0] == "key-not-accepted"


def test_an_unnamed_write_refusal_depends_on_the_protocol():
    state, detail = attribute_git_error(
        "remote: Write access to repository not granted.")
    assert state == "write-not-granted"
    assert "Over SSH" in detail
    assert "remote URL" in repair(state)


def test_an_unknown_or_absent_message_is_not_invented():
    assert attribute_git_error("something else entirely")[0] == "unattributed"
    assert attribute_git_error("")[0] == "no-message"
    assert attribute_git_error(None)[0] == "no-message"


def test_the_inventory_reports_age_without_reporting_material():
    from datetime import datetime, timezone
    now = datetime(2026, 8, 31, tzinfo=timezone.utc)
    assert age_days("2021-06-02T11:03:00Z", now) == 1915
    assert age_days(None) is None
    assert age_days("not a date") is None
    stale = stale_keys([READ_ONLY, WRITABLE], DEFAULT_MAX_AGE_DAYS, now)
    assert len(stale) == 2
    assert stale[0]["age_days"] == 1915
    assert FAKE_MATERIAL not in json.dumps(stale)
    assert stale_keys([READ_ONLY], 10000, now) == []


def test_the_cost_is_worked_out_before_anything_is_fetched():
    assert read_cost(["a", "b"]) == 2
    assert read_cost([]) == 0
    assert read_cost(None) == 0
    assert DEFAULT_MAX_AGE_DAYS == 365
