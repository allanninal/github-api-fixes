import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  parseGrant, parseNeeds, permissionShortfall, rank, repair, repoGap, verdict,
} from './github-token-reach.mjs';

const MINT = {
  expires_at: '2026-08-30T13:00:00Z',
  permissions: { contents: 'read', metadata: 'read' },
  repository_selection: 'selected',
  repositories: [{ full_name: 'acme/api' }],
};

test('a bare permission name means read', () => {
  assert.deepEqual(parseNeeds('contents, issues:write'),
    { contents: 'read', issues: 'write' });
  assert.deepEqual(parseNeeds(''), {});
  assert.deepEqual(parseNeeds('  ,  '), {});
});

test('levels are ranked rather than compared as strings', () => {
  assert.ok(rank('write') > rank('read'));
  assert.ok(rank('read') > rank(null));
  assert.equal(rank('nonsense'), 0);
});

test('a mint response is read for the three fields that matter', () => {
  const grant = parseGrant(MINT);
  assert.deepEqual(grant.permissions, { contents: 'read', metadata: 'read' });
  assert.equal(grant.repository_selection, 'selected');
  assert.deepEqual(grant.repositories, ['acme/api']);
});

test('a mint response with no permissions block is unseen not empty', () => {
  assert.equal(parseGrant({ repository_selection: 'all' }).permissions, null);
  assert.equal(parseGrant(null).permissions, null);
});

test('repository names match regardless of case', () => {
  assert.deepEqual(repoGap(['acme/API'], ['Acme/api']), []);
  assert.deepEqual(repoGap(['acme/api'], ['acme/api', 'acme/docs']), ['acme/docs']);
});

test('an unseen grant is not a permission pass', () => {
  assert.equal(permissionShortfall(null, { issues: 'write' }), null);
  assert.deepEqual(permissionShortfall({}, { issues: 'write' }),
    [['issues', 'write', 'absent']]);
});

test('read where write is needed is a shortfall', () => {
  assert.deepEqual(permissionShortfall({ issues: 'read' }, { issues: 'write' }),
    [['issues', 'write', 'read']]);
  assert.deepEqual(permissionShortfall({ issues: 'write' }, { issues: 'read' }), []);
});

test('an unreachable repository is reported before a permission shortfall', () => {
  const [state, detail] = verdict(true, ['acme/docs'], [['issues', 'write', 'read']], 'selected');
  assert.equal(state, 'repos-out-of-reach');
  assert.match(detail, /acme\/docs/);
});

test('a permission shortfall names both levels', () => {
  const [state, detail] = verdict(true, [], [['issues', 'write', 'read']], 'all');
  assert.equal(state, 'permissions-below-need');
  assert.match(detail, /issues is read, the job needs write/);
});

test('an unseen grant gets its own state rather than a clean bill', () => {
  const [state, detail] = verdict(true, [], null, 'all');
  assert.equal(state, 'narrowing-not-visible');
  assert.match(detail, /does not report its own permission map/);
});

test('a narrowed token that still covers the job is not a fault', () => {
  assert.equal(verdict(true, [], [], 'selected')[0], 'narrowed-but-sufficient');
});

test('a wide token that covers the job is clean', () => {
  assert.equal(verdict(true, [], [], 'all')[0], 'reach-covers-the-job');
});

test('a dead token is never reported as a narrowing', () => {
  const [state, detail] = verdict(false, ['acme/docs'], null, null);
  assert.equal(state, 'token-not-alive');
  assert.match(detail, /does not arise yet/);
});

test('the repair points at the mint request and not at the app', () => {
  const text = repair('repos-out-of-reach', ['acme/docs'], null);
  assert.match(text, /token request/);
  assert.match(text, /the App does not change/);
  assert.match(repair('narrowing-not-visible', [], null), /mint response/);
  assert.equal(repair('reach-covers-the-job', [], []),
    'nothing. This token is not the constraint.');
});
