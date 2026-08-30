import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  ACTION_MINIMUM, ROLES, blockedActions, can, deficit, readCost, repair,
  roleFromCollaborator, roleFromPermissions, roleRank, scopeList,
  scopesAreTheCeiling, tokenKind, verdict,
} from './github-repo-role.mjs';

const READ_ONLY = {
  admin: false, maintain: false, push: false, triage: false, pull: true,
};
const TRIAGE = {
  admin: false, maintain: false, push: false, triage: true, pull: true,
};
const WRITE = {
  admin: false, maintain: false, push: true, triage: true, pull: true,
};

test('the hierarchy runs weakest to strongest', () => {
  assert.deepEqual(ROLES, ['none', 'read', 'triage', 'write', 'maintain', 'admin']);
  assert.ok(roleRank('read') < roleRank('triage'));
  assert.ok(roleRank('write') < roleRank('admin'));
  assert.equal(roleRank('nonsense'), -1);
});

test('the role is the highest true flag', () => {
  assert.equal(roleFromPermissions(READ_ONLY), 'read');
  assert.equal(roleFromPermissions(TRIAGE), 'triage');
  assert.equal(roleFromPermissions(WRITE), 'write');
});

test('an absent permissions object is unreported not none', () => {
  assert.equal(roleFromPermissions({}), 'unreported');
  assert.equal(roleFromPermissions(null), 'unreported');
  assert.equal(roleFromPermissions({ admin: false, pull: false }), 'none');
});

test('read explains every refused write in one flag', () => {
  assert.equal(READ_ONLY.push, false);
  assert.equal(can('read', 'merge-pull-request'), false);
  assert.equal(can('read', 'read-code'), true);
});

test('labelling needs triage and not write', () => {
  assert.equal(ACTION_MINIMUM['label-issue'], 'triage');
  assert.equal(can('triage', 'label-issue'), true);
  assert.equal(can('triage', 'merge-pull-request'), false);
  assert.equal(deficit('triage', 'merge-pull-request'), 1);
});

test('the deficit counts roles not booleans', () => {
  assert.equal(deficit('read', 'merge-pull-request'), 2);
  assert.equal(deficit('read', 'add-collaborator'), 4);
  assert.equal(deficit('admin', 'merge-pull-request'), 0);
  assert.equal(deficit('read', 'not-an-action'), null);
});

test('the legacy permission field rounds two roles away', () => {
  assert.deepEqual(
    roleFromCollaborator({ permission: 'write', role_name: 'maintain' }).slice(0, 2),
    ['maintain', true],
  );
  const [rounded, exact, note] = roleFromCollaborator({ permission: 'write' });
  assert.equal(rounded, 'write');
  assert.equal(exact, false);
  assert.match(note, /rounds maintain to write/);
  assert.equal(roleFromCollaborator({ role_name: 'triage' })[0], 'triage');
});

test('a custom org role is named and not priced', () => {
  const [role, exact, note] = roleFromCollaborator(
    { permission: 'read', role_name: 'security-auditor' },
  );
  assert.equal(role, 'custom:security-auditor');
  assert.equal(exact, false);
  assert.match(note, /custom organization role/);
  assert.equal(verdict(role, 'merge-pull-request')[0], 'custom-role');
});

test('a repo scope beside a read role is the headline', () => {
  const [state, detail] = scopesAreTheCeiling(
    'read', ['repo', 'workflow'], 'classic PAT', 'merge-pull-request');
  assert.equal(state, 'scopes-are-not-the-ceiling');
  assert.match(detail, /cannot change this answer/);
});

test('a fine grained token has no scopes to widen', () => {
  const [state, detail] = scopesAreTheCeiling(
    'read', null, 'fine-grained PAT', 'merge-pull-request');
  assert.equal(state, 'no-scopes-to-widen');
  assert.match(detail, /nothing to widen/);
});

test('a narrow scope and a low role are both reported', () => {
  const [state, detail] = scopesAreTheCeiling(
    'read', ['public_repo'], 'classic PAT', 'merge-pull-request');
  assert.equal(state, 'two-gates-open');
  assert.match(detail, /both/);
});

test('a sufficient role sends the reader to the credential', () => {
  assert.equal(
    scopesAreTheCeiling('write', ['repo'], 'classic PAT', 'merge-pull-request')[0],
    'not-the-question',
  );
  assert.equal(verdict('write', 'merge-pull-request')[0], 'role-sufficient');
});

test('the verdict and its repair hang together', () => {
  const [state, detail] = verdict('read', 'merge-pull-request');
  assert.equal(state, 'role-insufficient');
  assert.match(detail, /2 role\(s\) higher/);
  const fix = repair(state, 'read', 'merge-pull-request', 'octobot');
  assert.match(fix, /octobot/);
  assert.match(fix, /never its source/);
});

test('no access is kept apart from a low role', () => {
  const [state, detail] = verdict('none', 'merge-pull-request');
  assert.equal(state, 'no-access');
  assert.match(detail, /404/);
  assert.equal(verdict('unreported', 'merge-pull-request')[0], 'role-unreported');
});

test('the blocked list grows as the role shrinks', () => {
  assert.ok(blockedActions('read').includes('merge-pull-request'));
  assert.ok(blockedActions('read').includes('label-issue'));
  assert.ok(!blockedActions('triage').includes('label-issue'));
  assert.deepEqual(blockedActions('admin'), []);
});

test('scopes absent and scopes empty are different readings', () => {
  assert.equal(scopeList(null), null);
  assert.deepEqual(scopeList(''), []);
  assert.deepEqual(scopeList('repo, workflow'), ['repo', 'workflow']);
});

test('the credential type comes from its prefix', () => {
  assert.equal(tokenKind('ghp_x'), 'classic PAT');
  assert.equal(tokenKind('github_pat_x'), 'fine-grained PAT');
  assert.equal(tokenKind('nope'), 'unknown');
});

test('the run costs two reads or three', () => {
  assert.equal(readCost(), 2);
  assert.equal(readCost(true), 3);
});
