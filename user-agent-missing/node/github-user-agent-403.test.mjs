import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  classify403, gradeUserAgent, suggestUserAgent, verdict,
} from './github-user-agent-403.mjs';

test('an absent header is not an empty one', () => {
  assert.equal(gradeUserAgent(null)[0], 'absent');
  assert.equal(gradeUserAgent('')[0], 'empty');
  assert.equal(gradeUserAgent('   ')[0], 'empty');
});

test('a library default satisfies the rule and identifies nobody', () => {
  assert.equal(gradeUserAgent('python-requests/2.31.0')[0], 'library-default');
  assert.equal(gradeUserAgent('Go-http-client/1.1')[0], 'library-default');
  assert.equal(gradeUserAgent('curl/8.4.0')[0], 'library-default');
});

test('a named application with a version and a contact is descriptive', () => {
  assert.equal(gradeUserAgent('acme-repo-auditor/1.2 (+https://acme.example)')[0],
    'descriptive');
});

test('half an identity is reported as half', () => {
  assert.equal(gradeUserAgent('acme-repo-auditor/1.2')[0], 'named');
  assert.equal(gradeUserAgent('acme (+https://acme.example)')[0], 'named');
  assert.equal(gradeUserAgent('auditor')[0], 'opaque');
});

test('the user-agent rule names itself in the body', () => {
  const [state, detail] = classify403(
    'Request forbidden by administrative rules. Please make sure your '
    + 'request has a User-Agent header.', {});
  assert.equal(state, 'user-agent-rule');
  assert.ok(detail.includes('User-Agent'));
});

test('quota exhaustion is read from a header not from words', () => {
  assert.equal(classify403('API rate limit exceeded',
    { 'X-RateLimit-Remaining': '0' })[0], 'primary-rate-limit');
});

test('a secondary limit is not confused with the primary one', () => {
  assert.equal(classify403('You have exceeded a secondary rate limit',
    { 'x-ratelimit-remaining': '4998' })[0], 'secondary-rate-limit');
});

test('a permission refusal is sorted away from this page', () => {
  assert.equal(classify403('Resource not accessible by integration', {})[0],
    'permission');
});

test('an unfamiliar 403 is admitted rather than guessed', () => {
  assert.equal(classify403('Something new', {})[0], 'unclassified-403');
});

test('the missing header verdict says what was actually sent', () => {
  const [state, detail] = verdict(403,
    'Request forbidden by administrative rules. Please make sure your '
    + 'request has a User-Agent header.', {}, null);
  assert.equal(state, 'user-agent-missing');
  assert.ok(detail.includes('nothing'));
});

test('a quota 403 is not reported as a header problem', () => {
  const [state, detail] = verdict(403, 'API rate limit exceeded',
    { 'x-ratelimit-remaining': '0' }, 'acme/1.0 (+http://a)');
  assert.equal(state, 'primary-rate-limit');
  assert.ok(detail.includes('no User-Agent will repair it'));
});

test('a 401 is sent to the credential notes', () => {
  assert.equal(verdict(401, 'Bad credentials', {}, 'acme/1.0')[0],
    'not-a-user-agent-problem');
});

test('a successful request with a default agent is still a finding', () => {
  assert.equal(verdict(200, null, {}, 'python-requests/2.31.0')[0],
    'identifiable-agent-missing');
});

test('a successful request with a descriptive agent passes', () => {
  assert.equal(verdict(200, null, {}, 'acme-auditor/1.2 (+https://acme.example)')[0],
    'user-agent-ok');
});

test('the suggested header always grades as descriptive', () => {
  const agent = suggestUserAgent('Acme Repo Auditor!', '1.2',
    'https://acme.example/contact');
  assert.equal(agent, 'acme-repo-auditor/1.2 (+https://acme.example/contact)');
  assert.equal(gradeUserAgent(agent)[0], 'descriptive');
});

test('an unnameable application still produces a usable header', () => {
  assert.ok(suggestUserAgent('!!!').startsWith('unnamed-integration/'));
});
