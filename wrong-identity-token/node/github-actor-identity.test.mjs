import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  attributed, classify, couplings, humanSignals, identity,
  looksLikeAPersonName, machineShaped,
} from './github-actor-identity.mjs';

const PERSON = {
  login: 'jdoe', type: 'User', name: 'Jane Doe', bio: 'SRE',
  hireable: true, followers: 137,
};
const APP = { login: 'acme-deploy[bot]', type: 'Bot', name: 'acme-deploy' };
const BARE = { login: 'wj4', type: 'User', name: null, followers: 0 };

test('a profile reduces to login, type and name', () => {
  assert.deepEqual(identity(PERSON), { login: 'jdoe', type: 'User', name: 'Jane Doe' });
});

test('a body with no login is not an identity', () => {
  assert.equal(identity(null), null);
  assert.equal(identity({ message: 'Resource not accessible by integration' }), null);
  assert.equal(identity([]), null);
});

test('a personal name needs two capitalised words', () => {
  assert.ok(looksLikeAPersonName('Jane Doe'));
  assert.ok(!looksLikeAPersonName('acme-deploy'));
  assert.ok(!looksLikeAPersonName('Jane'));
  assert.ok(!looksLikeAPersonName(null));
});

test('machine hints are matched as tokens and not as substrings', () => {
  assert.ok(machineShaped('acme-ci'));
  assert.ok(machineShaped('deploy_bot'));
  assert.ok(machineShaped('acme-deploy[bot]'));
  assert.ok(!machineShaped('cindy'));
  assert.ok(!machineShaped('abbotsford'));
});

test('a declared machine login beats the heuristic', () => {
  assert.ok(machineShaped('hermes', ['hermes']));
  assert.ok(!machineShaped('hermes'));
});

test('human signals are named individually', () => {
  const signals = humanSignals(PERSON);
  assert.ok(signals.some((s) => s.includes('personal name')));
  assert.ok(signals.some((s) => s.includes('bio')));
  assert.ok(signals.some((s) => s.includes('hireable')));
  assert.ok(signals.some((s) => s.includes('137 followers')));
});

test('an email is counted and never quoted', () => {
  const signals = humanSignals({ login: 'x', email: 'jane@acme.example' });
  assert.deepEqual(signals, ['a public email address is set']);
});

test('a quiet profile produces no signals', () => {
  assert.deepEqual(humanSignals(BARE), []);
  assert.deepEqual(humanSignals(null), []);
});

test('a bot identity is the healthy answer', () => {
  const [state, detail] = classify(identity(APP), [], true);
  assert.equal(state, 'app-installation');
  assert.match(detail, /employment/);
  assert.deepEqual(couplings(state), []);
});

test('a person behind the automation is the finding', () => {
  const [state, detail] = classify(identity(PERSON), humanSignals(PERSON), false);
  assert.equal(state, 'personal-account');
  assert.match(detail, /running as a person/);
  assert.ok(couplings(state).some((c) => c.includes('deprovisioning')));
});

test('a machine login with a human profile is its own state', () => {
  const body = { ...PERSON, login: 'acme-ci' };
  const [state, detail] = classify(identity(body), humanSignals(body), true);
  assert.equal(state, 'mixed-signals');
  assert.match(detail, /renamed/);
});

test('a clean machine account is a compromise rather than a pass', () => {
  const [state, detail] = classify(identity({ login: 'acme-ci', type: 'User' }), [], true);
  assert.equal(state, 'machine-account');
  assert.match(detail, /still an account with a seat/);
});

test('an unreadable identity is reported as the healthy case', () => {
  const [state, detail] = classify(null, [], false);
  assert.equal(state, 'identity-unreadable');
  assert.match(detail, /no user behind it/);
});

test('a bare user account is not guessed at', () => {
  const [state, detail] = classify(identity(BARE), [], false);
  assert.equal(state, 'unclassified-user');
  assert.match(detail, /will not guess/);
});

test('attribution separates mine, theirs and unlinked', () => {
  const commits = [
    { author: { login: 'jdoe' } },
    { author: { login: 'JDOE' } },
    { author: { login: 'someone' } },
    { author: null },
    {},
  ];
  assert.deepEqual(attributed(commits, 'jdoe'), { total: 5, attributed: 2, unlinked: 2 });
  assert.deepEqual(attributed(null, 'jdoe'), { total: 0, attributed: 0, unlinked: 0 });
});
