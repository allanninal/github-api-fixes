import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  authorizationState, cadenceNote, daysLeft, lapseEvidence, lastEight,
  matchAuthorization, parseTs, readCost, repair, tokenKind, unattendedVerdict,
} from './github-sso-session-clock.mjs';

const NOW = Date.parse('2026-08-31T12:00:00Z');

function record(days, tail = 'fake1234', used = '2026-08-31T04:11:07Z') {
  return {
    credential_id: 161195,
    credential_type: 'personal access token',
    token_last_eight: tail,
    credential_accessed_at: used,
    authorized_credential_expires_at: new Date(NOW + days * 86400000).toISOString(),
  };
}

test('without a record a lapse and a first authorization are the same', () => {
  assert.equal(authorizationState(null, NOW, true)[0], 'never-authorized');
  assert.equal(authorizationState(null, NOW, false)[0], 'no-record-no-refusal');
});

test('the clock produces three different sentences', () => {
  assert.equal(authorizationState(record(-1), NOW, true)[0], 'authorization-lapsed');
  assert.equal(authorizationState(record(3), NOW, false)[0], 'authorization-expiring');
  assert.equal(authorizationState(record(30), NOW, false)[0], 'authorization-active');
});

test('the expiring verdict is a forecast with a number on it', () => {
  const [, detail] = authorizationState(record(3), NOW, false);
  assert.ok(detail.includes('3 day(s)'));
  assert.equal(daysLeft(record(3).authorized_credential_expires_at, NOW), 3);
  assert.equal(daysLeft(record(-2).authorized_credential_expires_at, NOW), -2);
});

test('a record with no expiry is not reported as active', () => {
  assert.equal(authorizationState({ token_last_eight: 'fake1234' }, NOW, true)[0],
    'expiry-not-published');
});

test('the match runs on the last eight and nothing else', () => {
  const tail = lastEight('ghp_fake1234');
  assert.equal(tail, 'fake1234');
  const records = [record(9, 'other000'), record(2, tail)];
  assert.equal(matchAuthorization(records, tail).token_last_eight, tail);
  assert.equal(matchAuthorization(records, 'nomatch0'), null);
  assert.equal(lastEight('short'), '');
});

test('the last eight never reaches the report', () => {
  const tail = lastEight('ghp_fake1234');
  const matched = matchAuthorization([record(5, tail)], tail);
  const [state, detail] = authorizationState(matched, NOW, false);
  const report = JSON.stringify({
    state, detail, record_matched: Boolean(matched),
    repair: repair(state, 'acme-corp', 'classic PAT'),
    cadence: cadenceNote(state),
  });
  assert.ok(!report.includes(tail));
});

test('past use is what proves this was a lapse', () => {
  assert.equal(lapseEvidence(record(-1))[0], true);
  assert.equal(lapseEvidence(null)[0], false);
  assert.equal(lapseEvidence(record(-1, 'fake1234', null))[0], false);
});

test('the cadence is reported as inferred not measured', () => {
  assert.ok(cadenceNote('authorization-expiring').includes('not published'));
  assert.equal(cadenceNote('no-record-no-refusal'), 'nothing to forecast from this reading.');
});

test('an installation token is the only one that does not lapse', () => {
  assert.equal(unattendedVerdict('classic PAT')[0], true);
  assert.equal(unattendedVerdict('App installation token')[0], false);
});

test('the repair renews and then says stop renewing', () => {
  const fix = repair('authorization-expiring', 'acme-corp', 'classic PAT');
  assert.ok(fix.includes('https://github.com/orgs/acme-corp/sso'));
  assert.ok(fix.includes('does not and will not do it'));
  assert.ok(fix.includes('App installation token'));
  assert.ok(!repair('authorization-expiring', 'acme-corp', 'App installation token')
    .includes('App installation token'));
});

test('timestamps survive both spellings of utc', () => {
  assert.equal(parseTs('2026-09-03T09:22:41Z'), parseTs('2026-09-03T09:22:41+00:00'));
  assert.equal(parseTs('not a date'), null);
});

test('the credential type comes from its prefix', () => {
  assert.equal(tokenKind('ghp_fake'), 'classic PAT');
  assert.equal(tokenKind('ghs_fake'), 'App installation token');
});

test('the run costs two reads plus the record pages', () => {
  assert.equal(readCost(false), 2);
  assert.equal(readCost(true, 3), 5);
});
