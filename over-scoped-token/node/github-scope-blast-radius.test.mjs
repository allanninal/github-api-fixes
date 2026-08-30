import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  blastRadius, capabilities, excess, heldScopes, required, verdict,
} from './github-scope-blast-radius.mjs';

const USER = { login: 'octo-bot', public_repos: 12, total_private_repos: 88 };

test('an absent scope header means a fine-grained credential', () => {
  const [scopes, kind] = heldScopes({ 'X-RateLimit-Limit': '5000' });
  assert.equal(scopes, null);
  assert.equal(kind, 'not-scope-based');
});

test('the header is read case-insensitively', () => {
  const [scopes, kind] = heldScopes({ 'X-OAuth-Scopes': 'repo, delete_repo' });
  assert.deepEqual(scopes, ['repo', 'delete_repo']);
  assert.equal(kind, 'scope-based');
});

test('a token minted with no scopes is not the same as no header', () => {
  const [scopes, kind] = heldScopes({ 'x-oauth-scopes': '' });
  assert.deepEqual(scopes, []);
  assert.equal(kind, 'scope-based');
});

test('reading public data requires no scope at all', () => {
  assert.deepEqual(required(['public-repos']).classic, []);
  assert.deepEqual(required(['public-repos']).fine_grained, ['Metadata: Read']);
});

test('a private read needs the broadest classic scope there is', () => {
  assert.deepEqual(required(['pull-requests']).classic, ['repo']);
});

test('an unrecognised read is reported rather than dropped', () => {
  const out = required(['pull-requests', 'telemetry']);
  assert.deepEqual(out.unknown, ['telemetry']);
  assert.deepEqual(out.classic, ['repo']);
});

test('capabilities are verbs and deduplicated', () => {
  const verbs = capabilities(['repo', 'public_repo', 'delete_repo']);
  assert.ok(verbs.some((v) => v.includes('permanently remove')));
  assert.equal(verbs.length, new Set(verbs).size);
});

test('read-only scopes authorize no verbs', () => {
  assert.deepEqual(capabilities(['read:org', 'read:packages']), []);
});

test('excess is everything outside the minimum', () => {
  assert.deepEqual(excess(['repo', 'delete_repo', 'read:org'], ['repo']),
    ['delete_repo', 'read:org']);
});

test('blast radius counts public and private together', () => {
  const radius = blastRadius(USER, ['repo']);
  assert.equal(radius.repositories, 100);
  assert.deepEqual(radius.write_scopes, ['repo']);
});

test('a body without counts reports no number rather than zero', () => {
  assert.equal(blastRadius({}, ['repo']).repositories, null);
});

test('a fine-grained token is the pass condition', () => {
  const [state, detail] = verdict('not-scope-based', null, required([]),
    blastRadius(USER, null));
  assert.equal(state, 'not-scope-based');
  assert.match(detail, /nothing to narrow/);
});

test('a read-only job holding delete_repo is flagged', () => {
  const held = ['repo', 'delete_repo', 'workflow'];
  const [state, detail] = verdict('scope-based', held, required(['pull-requests']),
    blastRadius(USER, held));
  assert.equal(state, 'over-scoped');
  assert.match(detail, /delete_repo/);
  assert.match(detail, /100 repositories/);
});

test('unused read scopes are untidy rather than dangerous', () => {
  const held = ['repo', 'read:packages'];
  const [state, detail] = verdict('scope-based', held, required(['pull-requests']),
    blastRadius(USER, held));
  assert.equal(state, 'unused-scopes');
  assert.match(detail, /untidy/);
});

test('the minimum classic scope is still reported as too broad', () => {
  const held = ['repo'];
  const [state, detail] = verdict('scope-based', held, required(['pull-requests']),
    blastRadius(USER, held));
  assert.equal(state, 'coarse-by-construction');
  assert.match(detail, /different credential type/);
});

test('a genuinely minimal token is clean', () => {
  const held = ['read:org'];
  const [state] = verdict('scope-based', held, required(['org-members']),
    blastRadius(USER, held));
  assert.equal(state, 'least-privilege');
});
