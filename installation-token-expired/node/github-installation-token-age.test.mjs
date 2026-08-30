import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  DANGER_BAND, LIFETIME, classify, cliffAt, interpret, parseExpiryHeader,
  parseMoment, reconcile, refreshVerdict, remaining,
} from './github-installation-token-age.mjs';

const NOW = 1772000000;

/** Obviously not a credential. */
const FAKE = 'tok';

test('a recorded mint time parses in either shape', () => {
  assert.equal(parseMoment('1772000000'), NOW);
  assert.equal(parseMoment('2026-02-25T08:33:20Z'), parseMoment('1772008400'));
  assert.equal(parseMoment(''), null);
  assert.equal(parseMoment(null), null);
  assert.equal(parseMoment('some time last tuesday'), null);
});

test('the expiry github states is not iso 8601', () => {
  assert.equal(parseExpiryHeader('2026-02-25 08:33:20 UTC'), parseMoment('1772008400'));
  assert.equal(parseExpiryHeader(''), null);
  assert.equal(parseExpiryHeader(null), null);
});

test('the remaining life names the source it came from', () => {
  assert.deepEqual(remaining(NOW - 600, null, NOW), [3000, 'record']);
  assert.deepEqual(remaining(NOW - 600, NOW + 120, NOW), [120, 'github']);
  assert.deepEqual(remaining(null, null, NOW), [null, 'nothing']);
});

test('an hour old token is the headline finding', () => {
  const [state, detail] = classify(-60);
  assert.equal(state, 'expired');
  assert.match(detail, /60s ago/);
  assert.match(detail, /all at once/);
});

test('the bands below an hour are named separately', () => {
  assert.equal(classify(3000)[0], 'fresh');
  assert.equal(classify(599)[0], 'past-the-safe-margin');
  assert.equal(classify(DANGER_BAND - 1)[0], 'inside-the-danger-band');
  assert.equal(classify(0)[0], 'expired');
});

test('nothing recorded is a state and not a guess', () => {
  const [state, detail] = classify(null);
  assert.equal(state, 'no-record');
  assert.match(detail, /Record the moment you mint/);
});

test('minting once at startup is found before it fires', () => {
  const [state, detail] = refreshVerdict(0);
  assert.equal(state, 'minted-once-at-startup');
  assert.match(detail, /60 minutes after start/);
  assert.equal(refreshVerdict(null)[0], 'minted-once-at-startup');
});

test('an hourly timer against an hourly token is a race', () => {
  const [state, detail] = refreshVerdict(LIFETIME);
  assert.equal(state, 'refresh-slower-than-lifetime');
  assert.match(detail, /it is a race/);
  assert.equal(refreshVerdict(7200)[0], 'refresh-slower-than-lifetime');
});

test('a refresh with no room for a retry is still flagged', () => {
  assert.equal(refreshVerdict(3400)[0], 'refresh-without-margin');
});

test('fifty minutes is the schedule that passes', () => {
  const [state, detail] = refreshVerdict(3000);
  assert.equal(state, 'refresh-healthy');
  assert.match(detail, /600s of margin/);
});

test('the cliff is an hour after the mint', () => {
  assert.equal(cliffAt(NOW), NOW + LIFETIME);
  assert.equal(cliffAt(null), null);
});

test('two records of different tokens are caught', () => {
  const [state, detail] = reconcile(NOW + 240, NOW + 1440);
  assert.equal(state, 'record-disagrees');
  assert.match(detail, /1200s apart/);
  assert.equal(reconcile(NOW + 240, NOW + 250)[0], 'record-agrees');
  assert.equal(reconcile(null, NOW + 240)[0], 'no-header');
  assert.equal(reconcile(NOW + 240, null)[0], 'header-only');
});

test('a 401 at the end of the hour is the expiry', () => {
  const [state, detail] = interpret(401, 'Bad credentials', -30);
  assert.equal(state, 'expired-as-predicted');
  assert.match(detail, /the arithmetic above/);
});

test('a 401 with most of the hour left is explicitly not this problem', () => {
  const [state, detail] = interpret(401, 'Bad credentials', 2400);
  assert.equal(state, 'not-an-expiry-problem');
  assert.match(detail, /revoked, truncated or never valid/);
});

test('a 401 with no record refuses to choose', () => {
  assert.equal(interpret(401, 'Bad credentials', null)[0],
    'expired-or-revoked-cannot-tell');
});

test('the other responses point somewhere else', () => {
  assert.equal(interpret(200, null, 3000)[0], 'token-live');
  assert.equal(interpret(403, 'Resource not accessible by integration', 3000)[0],
    'wrong-credential-class');
  assert.equal(interpret(404, 'Not Found', 3000)[0], 'route-not-answered');
  assert.equal(interpret(500, 'Server Error', 3000)[0], 'unrelated');
});

test('the fixture token is obviously not a credential', () => {
  assert.ok(FAKE.length < 20);
});
