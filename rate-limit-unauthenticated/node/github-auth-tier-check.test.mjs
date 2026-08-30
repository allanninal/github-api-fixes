import { test } from 'node:test';
import assert from 'node:assert/strict';
import { inspectSecret, tierFromLimit, diagnose } from './github-auth-tier-check.mjs';

const GOOD = {
  fingerprint: 'ghp_ (40 chars)',
  kind: 'classic personal access token',
  problems: [],
};

test('unset and empty are not the same finding', () => {
  assert.deepEqual(inspectSecret(undefined).problems, ['unset']);
  assert.deepEqual(inspectSecret('').problems, ['empty']);
  assert.deepEqual(inspectSecret('   ').problems, ['blank']);
});

test('a normal token reports a fingerprint and no problems', () => {
  const got = inspectSecret('ghp_' + 'A'.repeat(36));
  assert.deepEqual(got.problems, []);
  assert.equal(got.kind, 'classic personal access token');
  assert.equal(got.fingerprint, 'ghp_ (40 chars)');
});

test('the fingerprint never contains the token', () => {
  const secret = 'ghp_' + 'S3CR3T'.repeat(6);
  const got = inspectSecret(secret);
  assert.ok(!got.fingerprint.includes('S3CR3T'));
  assert.ok(!JSON.stringify(got).includes(secret));
});

test('a fine-grained token is recognised', () => {
  assert.match(inspectSecret('github_pat_' + 'B'.repeat(60)).kind, /^fine-grained/);
});

test('an app installation token is recognised', () => {
  assert.match(inspectSecret('ghs_' + 'C'.repeat(36)).kind, /installation/);
});

test('surrounding quotes survived the paste', () => {
  const got = inspectSecret(`"ghp_${'A'.repeat(36)}"`);
  assert.ok(got.problems.includes('quoted'));
  assert.equal(got.kind, 'classic personal access token');
});

test('the scheme word ended up in the variable', () => {
  const got = inspectSecret('Bearer ghp_' + 'A'.repeat(36));
  assert.ok(got.problems.includes('scheme-included'));
  assert.equal(got.kind, 'classic personal access token');
  assert.ok(inspectSecret('token ghp_x').problems.includes('scheme-included'));
});

test('a trailing newline from a file is reported', () => {
  assert.ok(inspectSecret('ghp_' + 'A'.repeat(36) + '\n').problems.includes('padded'));
});

test('the placeholder from the example file is caught', () => {
  const got = inspectSecret('your_token_here');
  assert.ok(got.problems.includes('unknown-prefix'));
  assert.ok(got.problems.includes('placeholder'));
});

test('a real token is never accused of being a placeholder', () => {
  assert.deepEqual(inspectSecret('ghp_xxx' + 'A'.repeat(33)).problems, []);
});

test('sixty is the only boundary that matters', () => {
  assert.equal(tierFromLimit(60)[0], 'anonymous');
  assert.equal(tierFromLimit(5000)[0], 'authenticated');
  assert.equal(tierFromLimit(15000)[0], 'enterprise');
  assert.equal(tierFromLimit(12500)[0], 'scaled');
  assert.equal(tierFromLimit(null)[0], 'unknown');
});

test('five thousand is reported as ambiguous rather than as a user', () => {
  assert.match(tierFromLimit(5000)[1], /App installation/);
});

test('a missing variable is named as such', () => {
  const [state, detail] = diagnose(60, 60, 401, inspectSecret(undefined));
  assert.equal(state, 'no-token');
  assert.match(detail, /not set/);
});

test('a token that is present but not arriving is a different state', () => {
  const [state, detail] = diagnose(60, 60, 401, GOOD);
  assert.equal(state, 'anonymous');
  assert.match(detail, /not arriving/);
});

test('the quoting problem is named in the anonymous verdict', () => {
  const secret = inspectSecret(`"ghp_${'A'.repeat(36)}"`);
  const [, detail] = diagnose(60, 60, 401, secret);
  assert.match(detail, /surrounding quotes/);
});

test('a rejected token is not reported as a missing one', () => {
  const [state, detail] = diagnose(5000, 60, 401, GOOD);
  assert.equal(state, 'token-rejected');
  assert.match(detail, /expired/);
});

test('a 403 points at SSO rather than at the tier', () => {
  const [state, detail] = diagnose(5000, 60, 403, GOOD);
  assert.equal(state, 'blocked');
  assert.match(detail, /SSO/);
});

test('the healthy case cites the control', () => {
  const [state, detail] = diagnose(5000, 60, 200, GOOD);
  assert.equal(state, 'authenticated');
  assert.match(detail, /control reports 60/);
});
