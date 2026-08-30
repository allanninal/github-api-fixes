import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_FINE_GRAINED_MAX_DAYS, capVerdict, daysBetween, expiryAbsentMeaning,
  grantedLifetimeDays, grantsOverCap, headerValue, orgProbeVerdict, parseStamp,
  policyApplies, readCost, repair, rotationFit, tokenKind, verdict,
} from './github-token-lifetime.mjs';

// Obviously fake and far shorter than any real credential.
const FINE = 'github_pat_FAKE';
const CLASSIC = 'ghp_FAKE';
const INSTALLATION = 'ghs_FAKE';

const NOW = 1800000000;
const DAY = 86400;

test('the documented header shape parses and so does the iso one', () => {
  assert.equal(parseStamp('2026-09-30 12:00:00 UTC'), 1790769600);
  assert.equal(parseStamp('2026-09-30T12:00:00Z'), 1790769600);
  assert.equal(parseStamp('2026-09-30'), 1790726400);
  assert.equal(parseStamp('not a date'), null);
  assert.equal(parseStamp(null), null);
  assert.equal(parseStamp('30/09/2026'), null);
});

test('the header is read case insensitively', () => {
  assert.equal(headerValue({ 'Github-Authentication-Token-Expiration': 'x' }), 'x');
  assert.equal(headerValue({ unrelated: 'y' }), null);
  assert.equal(headerValue(null), null);
});

test('the granted lifetime needs an issue date and says so', () => {
  assert.equal(Math.round(grantedLifetimeDays(NOW - 30 * DAY, NOW + 60 * DAY)), 90);
  assert.equal(grantedLifetimeDays(null, NOW + 60 * DAY), null);
  const [state, detail] = capVerdict(null, 90);
  assert.equal(state, 'lifetime-unknown');
  assert.match(detail, /without an issue date/);
});

test('a token over the cap is blocked not shortened', () => {
  const [state, detail] = capVerdict(366, 90);
  assert.equal(state, 'over-org-cap');
  assert.match(detail, /it does not shorten them/);
  assert.equal(capVerdict(60, 90)[0], 'within-org-cap');
});

test('an undeclared cap is not an absent one', () => {
  const [state, detail] = capVerdict(366, null);
  assert.equal(state, 'cap-not-declared');
  assert.match(detail, /no documented endpoint/);
});

test('the schedule that can never work is kept apart from the one off', () => {
  let [state, detail] = rotationFit(90, 80, 365);
  assert.equal(state, 'rotation-outlives-token');
  assert.match(detail, /once per cycle, forever/);
  [state, detail] = rotationFit(365, 20, 90);
  assert.equal(state, 'this-cycle-expires-first');
  assert.match(detail, /A one-off/);
  assert.equal(rotationFit(365, 300, 90)[0], 'fits');
  assert.equal(rotationFit(365, -1, 90)[0], 'already-expired');
  assert.equal(rotationFit(null, 300, null)[0], 'rotation-not-declared');
});

test('the two findings have two different repairs', () => {
  const recurring = verdict('within-org-cap', 'rotation-outlives-token', 'policy-applies');
  assert.equal(recurring[0], 'schedule-cannot-work');
  assert.match(recurring[1], /every cycle/);
  const once = verdict('within-org-cap', 'this-cycle-expires-first', 'policy-applies');
  assert.equal(once[0], 'rotate-early-this-once');
  assert.match(once[1], /Bring the rotation forward/);
  assert.match(repair('schedule-cannot-work', 365, 90), /GitHub App/);
  assert.match(repair('rotate-early-this-once', 90, null), /bring this rotation forward/);
});

test('being over the cap outranks the schedule', () => {
  assert.equal(verdict('over-org-cap', 'fits', 'policy-applies')[0], 'blocked-by-lifetime-policy');
});

test('the policy only governs one credential class', () => {
  assert.equal(policyApplies('fine-grained PAT')[0], 'policy-applies');
  assert.ok(policyApplies('fine-grained PAT')[1]
    .includes(String(DEFAULT_FINE_GRAINED_MAX_DAYS)));
  const [state, detail] = policyApplies('classic PAT');
  assert.equal(state, 'different-class');
  assert.match(detail, /auto-revocation note/);
  assert.equal(policyApplies('App installation token')[0], 'minted-hourly');
  assert.equal(policyApplies('unknown')[0], 'class-unknown');
  assert.equal(tokenKind(FINE), 'fine-grained PAT');
  assert.equal(tokenKind(CLASSIC), 'classic PAT');
  assert.equal(tokenKind(INSTALLATION), 'App installation token');
  assert.equal(tokenKind(''), 'unknown');
});

test('a wrong class ends the note rather than grading it', () => {
  const [state, detail] = verdict('cap-not-declared', 'fits', 'minted-hourly');
  assert.equal(state, 'minted-hourly');
  assert.match(detail, /not about your problem/);
  assert.match(repair('minted-hourly', null, null), /no action from this note/);
});

test('the missing header means different things per class', () => {
  assert.equal(expiryAbsentMeaning('classic PAT')[0], 'no-expiry-on-this-class');
  assert.match(expiryAbsentMeaning('classic PAT')[1], /larger exposure/);
  assert.equal(expiryAbsentMeaning('App installation token')[0], 'short-lived-by-design');
  assert.equal(expiryAbsentMeaning('fine-grained PAT')[0], 'expiry-not-reported');
});

test('the org probe reports a shape and names its rivals', () => {
  const [state, detail] = orgProbeVerdict(200, 403);
  assert.equal(state, 'refused-by-one-org');
  assert.match(detail, /Three things produce that shape/);
  assert.match(detail, /narrows the search rather than ending it/);
  assert.equal(orgProbeVerdict(200, 200)[0], 'org-reachable');
  assert.equal(orgProbeVerdict(401, 403)[0], 'credential-dead');
  assert.equal(orgProbeVerdict(200, null)[0], 'org-not-probed');
});

test('the fleet read sorts by which credential goes next', () => {
  const grants = [
    { owner: { login: 'carol' }, token_expires_at: '2026-12-01 00:00:00 UTC', token_expired: false },
    { owner: { login: 'alice' }, token_expires_at: null, token_expired: false },
    { owner: { login: 'bob' }, token_expires_at: '2026-06-01 00:00:00 UTC', token_expired: false },
  ];
  const rows = grantsOverCap(grants, 90, NOW);
  assert.deepEqual(rows.map((r) => r.owner), ['bob', 'carol', 'alice']);
  assert.equal(rows[rows.length - 1].no_expiry, true);
  assert.equal(rows[rows.length - 1].over_declared_cap, true);
  assert.deepEqual(grantsOverCap([], 90, NOW), []);
});

test('the free probe is counted as free', () => {
  assert.deepEqual(readCost(false, false), [1, 0]);
  assert.deepEqual(readCost(true, false), [2, 1]);
  assert.deepEqual(readCost(true, true), [3, 2]);
});

test('days between is signed and null safe', () => {
  assert.equal(daysBetween(NOW, NOW + DAY), 1);
  assert.equal(daysBetween(NOW, NOW - DAY), -1);
  assert.equal(daysBetween(null, NOW), null);
});
