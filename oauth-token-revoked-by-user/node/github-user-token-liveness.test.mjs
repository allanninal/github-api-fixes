import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  authorizeUrl, collectTokens, populationVerdict, retryDisposition, tokenResult,
} from './github-user-token-liveness.mjs';

const ENV = {
  GH_USER_TOKEN_BEN: 'gho_fake2',
  GH_USER_TOKEN_ALICE: 'gho_fake1',
  GITHUB_TOKEN: 'ghp_fake',
  GH_USER_TOKEN_EMPTY: '',
};

test('tokens are collected by prefix and sorted', () => {
  assert.deepEqual(collectTokens(ENV, 'GH_USER_TOKEN_').map(([n]) => n),
    ['GH_USER_TOKEN_ALICE', 'GH_USER_TOKEN_BEN']);
});

test('an unrelated variable is not collected', () => {
  assert.ok(collectTokens(ENV, 'GH_USER_TOKEN_').every(([n]) => n !== 'GITHUB_TOKEN'));
});

test('an empty value is not a stored token', () => {
  assert.ok(collectTokens(ENV, 'GH_USER_TOKEN_')
    .every(([n]) => n !== 'GH_USER_TOKEN_EMPTY'));
});

test('a 403 is not a revocation', () => {
  assert.equal(tokenResult(200), 'alive');
  assert.equal(tokenResult(401), 'rejected');
  assert.equal(tokenResult(403), 'forbidden');
  assert.equal(tokenResult(500), 'error');
});

test('one refusal among successes is that person', () => {
  const [state, detail] = populationVerdict([['a', 'alive'], ['b', 'rejected'], ['c', 'alive']]);
  assert.equal(state, 'individual-revocation');
  assert.ok(detail.includes('1 of 3'));
  assert.ok(detail.includes('b'));
});

test('every token refused at once is the application', () => {
  const [state, detail] = populationVerdict([['a', 'rejected'], ['b', 'rejected'], ['c', 'rejected']]);
  assert.equal(state, 'application-wide');
  assert.ok(detail.includes('do not coordinate'));
});

test('one stored token cannot separate the two causes', () => {
  const [state, detail] = populationVerdict([['a', 'rejected']]);
  assert.equal(state, 'single-token-inconclusive');
  assert.ok(detail.includes('cannot be separated'));
});

test('a healthy fleet says look elsewhere', () => {
  assert.equal(populationVerdict([['a', 'alive'], ['b', 'alive']])[0], 'all-healthy');
});

test('an empty fleet is not a verdict about users', () => {
  assert.equal(populationVerdict([])[0], 'no-tokens');
});

test('errors alone are not read as an application failure', () => {
  assert.equal(populationVerdict([['a', 'error'], ['b', 'error']])[0], 'all-healthy');
});

test('a revoked token is terminal rather than retryable', () => {
  const [disposition, detail] = retryDisposition('rejected');
  assert.equal(disposition, 'terminal');
  assert.ok(detail.includes('never recovers'));
});

test('a failed probe is the only retryable state', () => {
  assert.equal(retryDisposition('error')[0], 'retryable');
  assert.equal(retryDisposition('forbidden')[0], 'terminal');
  assert.equal(retryDisposition('alive')[0], 'none');
});

test('the authorize url carries the client id and the scopes', () => {
  const url = authorizeUrl('Iv1.example', ['repo', 'read:org']);
  assert.ok(url.startsWith('https://github.com/login/oauth/authorize?'));
  assert.ok(url.includes('client_id=Iv1.example'));
  assert.ok(url.includes('scope=repo+read%3Aorg'));
});

test('optional parameters are omitted rather than sent empty', () => {
  const url = authorizeUrl('Iv1.example');
  assert.ok(!url.includes('scope='));
  assert.ok(!url.includes('redirect_uri='));
  assert.ok(!url.includes('state='));
});

test('a redirect and a state are encoded', () => {
  const url = authorizeUrl('Iv1.example', ['repo'], 'https://app.example/cb', 'xyz');
  assert.ok(url.includes('redirect_uri=https%3A%2F%2Fapp.example%2Fcb'));
  assert.ok(url.includes('state=xyz'));
});
