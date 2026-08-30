import { test } from 'node:test';
import assert from 'node:assert/strict';
import { scopeList, tokenKind, verdict } from './github-404-triage.mjs';

const probe = (over = {}) => ({
  repo_status: 404, authenticated: true, scopes: ['repo'],
  token_kind: 'classic PAT', in_installation: null, ...over,
});

test('prefixes name the credential without sending it', () => {
  assert.equal(tokenKind('ghp_abc123'), 'classic PAT');
  assert.equal(tokenKind('github_pat_11ABCDE'), 'fine-grained PAT');
  assert.equal(tokenKind('ghs_installation'), 'App installation token');
  assert.equal(tokenKind('  gho_padded  '), 'OAuth user token');
  assert.equal(tokenKind('v1.0123deadbeef'), 'unknown');
  assert.equal(tokenKind(null), 'unknown');
});

test('an absent scopes header is not an empty one', () => {
  assert.equal(scopeList(null), null);
  assert.deepEqual(scopeList(''), []);
  assert.deepEqual(scopeList('repo, read:org'), ['repo', 'read:org']);
});

test('dead token beats every other reading', () => {
  const [state, detail] = verdict(probe({ authenticated: false, scopes: null }));
  assert.equal(state, 'bad-credentials');
  assert.match(detail, /public/);
});

test('a repository that answers is visible', () => {
  assert.equal(verdict(probe({ repo_status: 200 }))[0], 'visible');
});

test('a real 403 is reported as the honest one', () => {
  const [state, detail] = verdict(probe({ repo_status: 403 }));
  assert.equal(state, 'plain-403');
  assert.match(detail, /rate limit/);
});

test('classic token without repo scope names the scope', () => {
  const [state, detail] = verdict(probe({ scopes: ['public_repo'] }));
  assert.equal(state, 'missing-scope');
  assert.match(detail, /public_repo/);
});

test('no scopes at all is still a classic token', () => {
  const [state, detail] = verdict(probe({ scopes: [] }));
  assert.equal(state, 'missing-scope');
  assert.match(detail, /no scopes at all/);
});

test('missing scope header means a fine-grained token', () => {
  assert.equal(
    verdict(probe({ scopes: null, token_kind: 'fine-grained PAT' }))[0],
    'repository-not-granted');
});

test('app token outside the installation is its own state', () => {
  assert.equal(
    verdict(probe({ token_kind: 'App installation token', scopes: null,
                    in_installation: false }))[0],
    'not-in-installation');
});

test('app token inside the installation points at metadata', () => {
  const [state, detail] = verdict(probe({
    token_kind: 'App installation token', scopes: null, in_installation: true }));
  assert.equal(state, 'metadata-permission');
  assert.match(detail, /Metadata/);
});

test('the indistinguishable case stays indistinguishable', () => {
  const [state, detail] = verdict(probe());
  assert.equal(state, 'no-access-or-gone');
  assert.match(detail, /same 404/);
});
