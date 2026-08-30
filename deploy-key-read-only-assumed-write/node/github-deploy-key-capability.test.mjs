import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_MAX_AGE_DAYS, SAFE_FIELDS, ageDays, attributeGitError, capability,
  readCost, redact, redactAll, repair, staleKeys, verdict, writableKeys,
} from './github-deploy-key-capability.mjs';

// Obviously not a key. The point of the fixture is that it never comes back out.
const FAKE_MATERIAL = 'ssh-ed25519 FAKE';

const READ_ONLY = {
  id: 41288114,
  key: FAKE_MATERIAL,
  title: 'ci-fetch',
  read_only: true,
  created_at: '2021-06-02T11:03:00Z',
  verified: true,
  added_by: 'dana-ops',
};
const WRITABLE = {
  id: 55210987,
  key: FAKE_MATERIAL,
  title: 'release-runner',
  read_only: false,
  created_at: '2024-11-18T08:00:00Z',
  verified: true,
  added_by: 'build-bot',
};

test('the key material never leaves the script', () => {
  const reduced = redact(READ_ONLY);
  assert.ok(!('key' in reduced));
  assert.ok(!JSON.stringify(reduced).includes(FAKE_MATERIAL));
  assert.ok(!JSON.stringify(redactAll([READ_ONLY, WRITABLE])).includes(FAKE_MATERIAL));
  assert.ok(Object.keys(reduced).every((k) => SAFE_FIELDS.includes(k)));
  assert.equal(reduced.id, 41288114);
  assert.equal(reduced.read_only, true);
});

test('redaction survives junk without leaking it', () => {
  assert.deepEqual(redact(null), {});
  assert.deepEqual(redact('not a key'), {});
  assert.deepEqual(redactAll(null), []);
  assert.deepEqual(redactAll([null, 'x', READ_ONLY]), [redact(READ_ONLY)]);
});

test('the capability is a declared field not an experiment', () => {
  assert.equal(capability(READ_ONLY), 'read-only');
  assert.equal(capability(WRITABLE), 'read-write');
  assert.equal(capability({ id: 1 }), 'unknown');
  assert.equal(capability(null), 'unknown');
  assert.deepEqual(writableKeys([READ_ONLY, WRITABLE]), [55210987]);
  assert.deepEqual(writableKeys([READ_ONLY]), []);
});

test('a pushing job with only read only keys is the finding', () => {
  const [state, detail] = verdict(200, [READ_ONLY, READ_ONLY], true);
  assert.equal(state, 'write-needed-none-capable');
  assert.match(detail, /all 2 deploy key\(s\)/);
  assert.match(repair(state), /cannot be edited on an existing key/);
  assert.match(repair(state), /contents: write/);
});

test('the same keys are correct when nothing pushes', () => {
  const [state, detail] = verdict(200, [READ_ONLY], false);
  assert.equal(state, 'read-only-and-correct');
  assert.match(detail, /recommended arrangement/);
  assert.ok(repair(state).startsWith('nothing.'));
});

test('a write capable key is reported either way', () => {
  assert.equal(verdict(200, [READ_ONLY, WRITABLE], true)[0], 'write-capable-key-present');
  const [state, detail] = verdict(200, [READ_ONLY, WRITABLE], false);
  assert.equal(state, 'write-capable-but-unused');
  assert.match(detail, /standing grant/);
});

test('a refused listing is not an empty inventory', () => {
  const [state, detail] = verdict(403, [], true);
  assert.equal(state, 'keys-unreadable');
  assert.match(detail, /not the same as the repository having no keys/);
  assert.match(repair(state), /Do not record the keys as absent/);
  assert.equal(verdict(404, [], false)[0], 'keys-unreadable');
  assert.equal(verdict(null, [], false)[0], 'keys-unreadable');
});

test('no keys at all is its own answer', () => {
  const [state, detail] = verdict(200, [], true);
  assert.equal(state, 'no-deploy-keys');
  assert.match(detail, /authenticating with something else/);
  assert.match(repair(state), /which credential your clone actually uses/);
});

test('the read only message names the key itself', () => {
  const [state, detail] = attributeGitError(
    'ERROR: The key you are authenticating with has been marked as read only.');
  assert.equal(state, 'deploy-key-read-only');
  assert.match(detail, /not a scope, a token or SSH/);
});

test('three of the four messages send you somewhere else', () => {
  assert.equal(attributeGitError(
    'remote: error: GH006: Protected branch update failed')[0],
  'refused-by-branch-protection');
  assert.equal(attributeGitError(
    'remote: Repository was archived so is read-only.')[0], 'repository-archived');
  assert.equal(attributeGitError(
    'git@github.com: Permission denied (publickey).')[0], 'key-not-accepted');
});

test('an unnamed write refusal depends on the protocol', () => {
  const [state, detail] = attributeGitError(
    'remote: Write access to repository not granted.');
  assert.equal(state, 'write-not-granted');
  assert.match(detail, /Over SSH/);
  assert.match(repair(state), /remote URL/);
});

test('an unknown or absent message is not invented', () => {
  assert.equal(attributeGitError('something else entirely')[0], 'unattributed');
  assert.equal(attributeGitError('')[0], 'no-message');
  assert.equal(attributeGitError(null)[0], 'no-message');
});

test('the inventory reports age without reporting material', () => {
  const now = Date.parse('2026-08-31T00:00:00Z');
  assert.equal(ageDays('2021-06-02T11:03:00Z', now), 1915);
  assert.equal(ageDays(null), null);
  assert.equal(ageDays('not a date'), null);
  const stale = staleKeys([READ_ONLY, WRITABLE], DEFAULT_MAX_AGE_DAYS, now);
  assert.equal(stale.length, 2);
  assert.equal(stale[0].age_days, 1915);
  assert.ok(!JSON.stringify(stale).includes(FAKE_MATERIAL));
  assert.deepEqual(staleKeys([READ_ONLY], 10000, now), []);
});

test('the cost is worked out before anything is fetched', () => {
  assert.equal(readCost(['a', 'b']), 2);
  assert.equal(readCost([]), 0);
  assert.equal(readCost(null), 0);
  assert.equal(DEFAULT_MAX_AGE_DAYS, 365);
});
