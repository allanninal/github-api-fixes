import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  classifyPair, fingerprint, rotationWaste, tokenTtl, verdict,
} from './github-etag-credential-check.mjs';

const NOON = Date.parse('2026-08-30T12:00:00Z') / 1000;

test('a 304 that becomes a 200 under another credential is the finding', () => {
  const [state, detail] = classifyPair(304, 200);
  assert.equal(state, 'credential-scoped');
  assert.match(detail, /full price/);
});

test('a 304 under both credentials clears rotation', () => {
  assert.equal(classifyPair(304, 304)[0], 'shared');
});

test('a control that answers 200 is not a rotation result', () => {
  const [state, detail] = classifyPair(200, 200);
  assert.equal(state, 'not-cacheable');
  assert.match(detail, /If-None-Match/);
});

test('no second credential is unproven rather than clear', () => {
  const [state, detail] = classifyPair(304, null);
  assert.equal(state, 'unproven');
  assert.match(detail, /arithmetic, not a measurement/);
});

test('a second credential that cannot see the url is not a cache finding', () => {
  const [state, detail] = classifyPair(304, 404);
  assert.equal(state, 'inconclusive');
  assert.match(detail, /404/);
});

test('a control that did not complete stops the analysis', () => {
  assert.equal(classifyPair(null, 200)[0], 'inconclusive');
  assert.equal(classifyPair(500, 200)[0], 'inconclusive');
});

test('an hourly token rotates twenty-four times a day', () => {
  const waste = rotationWaste(40, 30, 3600);
  assert.equal(waste.rotations, 24);
  assert.equal(waste.per_rotation, 40);
  assert.equal(waste.daily, 960);
  assert.equal(waste.polls, 115200);
});

test('a credential that outlives the window costs nothing inside it', () => {
  const waste = rotationWaste(10, 60, 172800);
  assert.equal(waste.rotations, 0);
  assert.equal(waste.daily, 0);
});

test('the share is of one hour of quota, not of the day', () => {
  assert.equal(rotationWaste(2000, 60, 3600).hourly_share, 0.4);
});

test('a zero interval does not divide by zero', () => {
  assert.ok(rotationWaste(5, 0, 0).polls >= 0);
});

test('tokenTtl reads the Z suffix GitHub actually sends', () => {
  assert.equal(tokenTtl('2026-08-30T13:00:00Z', NOON), 3600);
  assert.equal(tokenTtl('2026-08-30T13:00:00+00:00', NOON), 3600);
});

test('an expired token is zero and an unreadable one is null', () => {
  assert.equal(tokenTtl('2026-08-30T11:00:00Z', NOON), 0);
  assert.equal(tokenTtl('next tuesday', NOON), null);
  assert.equal(tokenTtl(null, NOON), null);
});

test('a fleet-sized cache spends a quarter of an hour of quota per mint', () => {
  const [state, detail] = verdict('credential-scoped', rotationWaste(2000, 60, 3600));
  assert.equal(state, 'rotation-dominates');
  assert.match(detail, /40%/);
});

test('a small cache is still reported as a cost', () => {
  const [state, detail] = verdict('credential-scoped', rotationWaste(40, 30, 3600));
  assert.equal(state, 'rotation-costs');
  assert.match(detail, /960 a day/);
});

test('nothing is projected until the control behaves', () => {
  assert.equal(verdict('not-cacheable', rotationWaste(40, 30, 3600))[0], 'not-cacheable');
  assert.equal(verdict('shared', rotationWaste(40, 30, 3600))[0], 'shared');
});

test('the cache key is a digest and never the token', () => {
  const key = fingerprint('ghp_secretvalue');
  assert.ok(!key.includes('ghp_secretvalue'));
  assert.equal(key, fingerprint('ghp_secretvalue'));
  assert.notEqual(key, fingerprint('ghp_other'));
});
