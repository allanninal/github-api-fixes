import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  classify, parseAuthHeader, passwordRemoved, replacementHeader, scanSnippet, verdict,
} from './github-auth-scheme-check.mjs';

const FAKE_TOKEN = 'ghp_FAKE0000000001';
const FAKE_PASSWORD = 'hunter2';

const basic = (user, secret) =>
  `Basic ${Buffer.from(`${user}:${secret}`).toString('base64')}`;

test('a password and a token in the same shape classify differently', () => {
  assert.equal(classify(parseAuthHeader(basic('octocat', FAKE_PASSWORD))), 'password-basic');
  assert.equal(classify(parseAuthHeader(basic('octocat', FAKE_TOKEN))), 'token-basic');
});

test('a forty character hex secret is read as a legacy token', () => {
  assert.equal(classify(parseAuthHeader(basic('octocat', 'a'.repeat(40)))), 'token-basic');
});

test('the parser never returns the secret', () => {
  const parsed = parseAuthHeader(basic('octocat', FAKE_PASSWORD));
  assert.ok(!JSON.stringify(parsed).includes(FAKE_PASSWORD));
  assert.equal(parsed.secret_length, FAKE_PASSWORD.length);
  assert.equal(parsed.username_present, true);
});

test('bearer and token schemes are recognised', () => {
  assert.equal(classify(parseAuthHeader(`Bearer ${FAKE_TOKEN}`)), 'bearer');
  assert.equal(classify(parseAuthHeader(`token ${FAKE_TOKEN}`)), 'token-scheme');
});

test('the scheme is matched case-insensitively', () => {
  assert.equal(classify(parseAuthHeader(`BEARER ${FAKE_TOKEN}`)), 'bearer');
});

test('an absent header is no credential rather than an error', () => {
  assert.equal(classify(parseAuthHeader(null)), 'no-credential');
  assert.equal(classify(parseAuthHeader('   ')), 'no-credential');
});

test('a broken base64 payload is not reported as a password', () => {
  assert.equal(classify(parseAuthHeader('Basic not-base64!!')), 'undecodable-basic');
});

test('an unfamiliar scheme is named as such', () => {
  assert.equal(classify(parseAuthHeader('Negotiate abcdef')), 'unknown-scheme');
});

test('the retired mechanism message is recognised in a body', () => {
  assert.equal(passwordRemoved({
    message: 'Support for password authentication was removed. Please use a ' +
      'personal access token instead.',
  }), true);
  assert.equal(passwordRemoved({ message: 'Bad credentials' }), false);
});

test('the message match survives odd whitespace', () => {
  assert.equal(passwordRemoved({
    message: 'support   for password\nauthentication was removed',
  }), true);
});

test('a password header is never sent', () => {
  const [state, detail] = verdict('password-basic', null, null);
  assert.equal(state, 'password-basic');
  assert.match(detail, /Nothing was sent/);
});

test('a username and token is flagged even though it works', () => {
  const [state, detail] = verdict('token-basic', 200, { login: 'octo-bot' });
  assert.equal(state, 'token-basic');
  assert.match(detail, /on the way out/);
});

test('a correct scheme with a bad token is a different problem', () => {
  const [state, detail] = verdict('bearer', 401, { message: 'Bad credentials' });
  assert.equal(state, 'credential-rejected');
  assert.match(detail, /different problem/);
});

test('the retired message under a bearer header means something rewrites it', () => {
  const [state] = verdict('bearer', 401, {
    message: 'Support for password authentication was removed.',
  });
  assert.equal(state, 'password-removed-message');
});

test('a working bearer header is the pass', () => {
  assert.equal(verdict('bearer', 200, { login: 'octo-bot' })[0], 'ok');
});

test('call sites are found by shape and never quoted', () => {
  const text = [
    `curl -u octocat:${FAKE_PASSWORD} https://api.github.com/user`,
    'Invoke-WebRequest -Uri $u -Credential $c',
    'client = Client(username=u, password=p)',
    'curl -H "Authorization: Bearer $T" https://api.github.com/user',
  ].join('\n');
  const sites = scanSnippet(text);
  assert.deepEqual([...new Set(sites.map((s) => s.line))].sort(), [1, 2, 3]);
  assert.ok(!JSON.stringify(sites).includes(FAKE_PASSWORD));
});

test('the replacement is a header rather than a credential', () => {
  assert.equal(replacementHeader(), 'Authorization: Bearer $GITHUB_TOKEN');
});
