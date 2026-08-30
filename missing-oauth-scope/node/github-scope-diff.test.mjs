import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  alternatives, expand, parseScopes, satisfies, verdict,
} from './github-scope-diff.mjs';

test('an absent header is not an empty scope list', () => {
  assert.equal(parseScopes(null), null);
  assert.deepEqual(parseScopes(''), []);
  assert.deepEqual(parseScopes('repo, read:org'), ['repo', 'read:org']);
});

test('holding repo already holds the narrower repo scopes', () => {
  const have = expand(['repo']);
  assert.ok(have.has('public_repo'));
  assert.ok(have.has('repo:status'));
  assert.ok(have.has('security_events'));
});

test('implication is transitive', () => {
  const have = expand(['admin:org']);
  assert.ok(have.has('write:org'));
  assert.ok(have.has('read:org'));
});

test('expanding nothing is empty rather than an error', () => {
  assert.equal(expand(null).size, 0);
  assert.equal(expand([]).size, 0);
});

test('the accepted header is parsed as alternatives', () => {
  assert.deepEqual(alternatives('admin:repo_hook, write:repo_hook'),
    [['admin:repo_hook'], ['write:repo_hook']]);
});

test('an absent accepted header is not an empty one', () => {
  assert.equal(alternatives(null), null);
  assert.deepEqual(alternatives(''), []);
});

test('a token holding repo does not need public_repo added', () => {
  const [ok, options] = satisfies(['repo'], alternatives('public_repo'));
  assert.equal(ok, true);
  assert.deepEqual(options, []);
});

test('the narrowest workable alternative wins', () => {
  const [ok, options] = satisfies([], alternatives('repo, public_repo'));
  assert.equal(ok, false);
  assert.deepEqual(options[0], ['public_repo']);
});

test('an empty accepted list is satisfied by any token', () => {
  assert.deepEqual(satisfies([], []), [true, []]);
});

test('an absent accepted list cannot be judged', () => {
  assert.deepEqual(satisfies(['repo'], null), [null, []]);
});

test('a missing scope is named and the alternatives counted', () => {
  const [state, detail] = verdict(403, ['public_repo', 'read:org'],
    alternatives('admin:repo_hook, write:repo_hook'));
  assert.equal(state, 'missing-scope');
  assert.match(detail, /write:repo_hook/);
  assert.match(detail, /2 alternative\(s\)/);
});

test('a fine-grained credential is sent to the other note', () => {
  const [state, detail] = verdict(403, null, alternatives('repo'));
  assert.equal(state, 'not-a-scoped-credential');
  assert.match(detail, /x-accepted-github-permissions/);
});

test('an empty accepted header rules scope out entirely', () => {
  const [state, detail] = verdict(404, ['repo'], []);
  assert.equal(state, 'any-token-accepted');
  assert.match(detail, /no scope will fix it/);
});

test('an absent accepted header is its own state', () => {
  assert.equal(verdict(404, ['repo'], null)[0], 'endpoint-named-no-scopes');
});

test('a satisfied token that still failed points elsewhere', () => {
  const [state, detail] = verdict(404, ['repo'], alternatives('repo'));
  assert.equal(state, 'scope-satisfied');
  assert.match(detail, /another cause/);
});

test('a successful call has nothing to diff', () => {
  assert.equal(verdict(200, ['repo'], alternatives('repo'))[0], 'call-succeeded');
});
