import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  compare, diagnose, ladder, outcome, shape,
} from './github-credential-differential.mjs';

const DEAD = [['public', 'unauthenticated'], ['identity', 'unauthenticated'],
  ['repository', 'unauthenticated']];
const ALIVE = [['public', 'ok'], ['identity', 'ok'], ['repository', 'ok']];

test('the ladder only includes rungs it can probe', () => {
  assert.deepEqual(ladder(), [['public', '/'], ['identity', '/user']]);
  const rungs = ladder('acme/api', 'acme');
  assert.deepEqual(rungs[2], ['repository', '/repos/acme/api']);
  assert.deepEqual(rungs[3], ['organization', '/orgs/acme']);
});

test('status codes reduce to what they say about a credential', () => {
  assert.equal(outcome(200), 'ok');
  assert.equal(outcome(401), 'unauthenticated');
  assert.equal(outcome(403), 'forbidden');
  assert.equal(outcome(404), 'missing');
  assert.equal(outcome(500), 'other');
  assert.equal(outcome(0), 'error');
  assert.equal(outcome(null), 'error');
});

test('a total failure and a partial one have different names', () => {
  assert.equal(shape(DEAD), 'uniform-401');
  assert.equal(shape(ALIVE), 'healthy');
  assert.equal(shape([['public', 'ok'], ['identity', 'forbidden']]), 'selective');
  assert.equal(shape([['public', 'missing'], ['identity', 'forbidden']]), 'mixed');
  assert.equal(shape([]), 'nothing-probed');
});

test('a rung the control never ran does not count as agreement', () => {
  const rows = compare(ALIVE, [['public', 'ok']]);
  assert.equal(rows[0].agrees, true);
  assert.equal(rows[1].control, null);
  assert.equal(rows[1].agrees, false);
});

test('without a control the script declines to name a cause', () => {
  const [state, detail] = diagnose(DEAD);
  assert.equal(state, 'no-control');
  assert.match(detail, /expiry, revocation and a truncated string/);
});

test('a uniform 401 against a healthy control is the credential', () => {
  const [state, detail] = diagnose(DEAD, ALIVE);
  assert.equal(state, 'credential-is-the-variable');
  assert.match(detail, /eliminated/);
});

test('two dead credentials are not two expiries', () => {
  const [state, detail] = diagnose(DEAD, DEAD);
  assert.equal(state, 'both-dead');
  assert.match(detail, /same second/);
});

test('identical failures on one rung are the resource', () => {
  const suspect = [['public', 'ok'], ['identity', 'ok'], ['repository', 'missing']];
  const [state, detail] = diagnose(suspect, suspect.map((row) => [...row]));
  assert.equal(state, 'resource-changed');
  assert.match(detail, /repository/);
});

test('a credential that authenticates anything has not expired', () => {
  const suspect = [['public', 'ok'], ['identity', 'ok'], ['repository', 'forbidden']];
  const [state, detail] = diagnose(suspect, ALIVE);
  assert.equal(state, 'access-not-expiry');
  assert.match(detail, /has not expired/);
  assert.match(detail, /repository \(forbidden\)/);
});

test('a healthy suspect sends you somewhere else', () => {
  assert.equal(diagnose(ALIVE, ALIVE)[0], 'suspect-healthy');
  assert.equal(diagnose(ALIVE)[0], 'suspect-healthy');
});

test('disagreeing shapes are reported rather than narrated', () => {
  const suspect = [['public', 'missing'], ['identity', 'forbidden']];
  const control = [['public', 'ok'], ['identity', 'unauthenticated']];
  const [state, detail] = diagnose(suspect, control);
  assert.equal(state, 'mixed');
  assert.match(detail, /rather than picking a story/);
});

test('nothing probed is not a pass', () => {
  assert.equal(diagnose([], ALIVE)[0], 'nothing-probed');
});
