import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  dormancyState, keepaliveCron, marginDays, probeInterval, reapExposure,
  tokenClass,
} from './github-token-dormancy.mjs';

test('each prefix names its class', () => {
  assert.equal(tokenClass('ghp_fake'), 'classic');
  assert.equal(tokenClass('github_pat_fk'), 'fine-grained');
  assert.equal(tokenClass('ghs_fake'), 'installation');
  assert.equal(tokenClass('gho_fake'), 'oauth');
  assert.equal(tokenClass('ghu_fake'), 'oauth');
  assert.equal(tokenClass('0'.repeat(40)), 'classic');
  assert.equal(tokenClass('something'), 'unknown');
  assert.equal(tokenClass(null), 'absent');
});

test('an expiry header takes a credential out of scope', () => {
  const [state, detail] = reapExposure('classic', '2026-09-30 12:00:00 UTC');
  assert.equal(state, 'not-reapable-expiring');
  assert.ok(detail.includes('different check'));
});

test('a classic token with no expiry is the reapable class', () => {
  assert.equal(reapExposure('classic', null)[0], 'reapable');
});

test('the other classes die of other causes', () => {
  assert.equal(reapExposure('fine-grained', null)[0], 'not-reapable-fine-grained');
  assert.equal(reapExposure('installation', null)[0], 'not-reapable-short-lived');
  assert.equal(reapExposure('oauth', null)[0], 'not-reapable-oauth');
  assert.equal(reapExposure('unknown', null)[0], 'unknown-class');
});

test('margin is the window minus the cadence', () => {
  assert.equal(marginDays(1), 364);
  assert.equal(marginDays(90), 275);
  assert.equal(marginDays(365), 0);
});

test('an unknown cadence is not guessed at', () => {
  assert.equal(marginDays(null), null);
  assert.equal(marginDays('sometimes'), null);
});

test('an annual job has lost the race before it starts', () => {
  const [state, detail] = dormancyState(200, 'reapable', 365);
  assert.equal(state, 'reap-race-lost');
  assert.ok(detail.includes('before it is next needed'));
});

test('one day inside the window is still tight', () => {
  assert.equal(dormancyState(200, 'reapable', 364)[0], 'reap-race-tight');
});

test('a frequent job keeps its own credential alive', () => {
  assert.equal(dormancyState(200, 'reapable', 1)[0], 'covered');
  assert.equal(dormancyState(200, 'reapable', 90)[0], 'covered');
});

test('a reaped credential is already gone', () => {
  const [state, detail] = dormancyState(401, 'reapable', 1);
  assert.equal(state, 'already-gone');
  assert.ok(detail.includes('nothing to un-revoke'));
});

test('a credential out of scope is reported as such', () => {
  assert.equal(dormancyState(200, 'not-reapable-expiring', 365)[0], 'not-reapable');
});

test('an unknown class is treated as reapable', () => {
  assert.equal(dormancyState(200, 'unknown-class', 365)[0], 'reap-race-lost');
});

test('a missing cadence is its own state', () => {
  const [state, detail] = dormancyState(200, 'reapable', null);
  assert.equal(state, 'cadence-unknown');
  assert.ok(detail.includes('how often'));
});

test('a broken probe says nothing about the credential', () => {
  assert.equal(dormancyState(500, 'reapable', 1)[0], 'unreachable');
  assert.equal(dormancyState(null, 'reapable', 1)[0], 'unreachable');
});

test('the probe is never slower than a month', () => {
  assert.equal(probeInterval(365), 30);
  assert.equal(probeInterval(90), 30);
  assert.equal(probeInterval(7), 7);
  assert.equal(probeInterval(1), 1);
});

test('an unknown cadence gets the monthly probe', () => {
  assert.equal(probeInterval(null), 30);
});

test('the cadence becomes a crontab line', () => {
  assert.equal(keepaliveCron(1), '0 6 * * *');
  assert.equal(keepaliveCron(7), '0 6 * * 1');
  assert.equal(keepaliveCron(30), '0 6 1 * *');
});
