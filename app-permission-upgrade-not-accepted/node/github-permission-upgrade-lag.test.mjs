import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  classify, cohorts, eventGap, permissionGap, permissionSurplus, rank, verdict,
} from './github-permission-upgrade-lag.mjs';

const DECLARED = { contents: 'read', issues: 'write', checks: 'write' };
const EVENTS = ['push', 'issues', 'check_run'];

function install(id, login, permissions, events = null) {
  return { id, account: { login }, permissions, events: events ?? EVENTS };
}

test('levels are ordered and absent is the bottom', () => {
  assert.ok(rank('admin') > rank('write'));
  assert.ok(rank('write') > rank('read'));
  assert.ok(rank('read') > rank(null));
  assert.equal(rank('READ '), rank('read'));
});

test('an unrecognised level is treated as no access', () => {
  assert.equal(rank('superuser'), 0);
});

test('a present key at too low a level is still a gap', () => {
  const gaps = permissionGap(DECLARED, { contents: 'read', issues: 'read', checks: 'write' });
  assert.deepEqual(gaps, [['issues', 'write', 'read']]);
});

test('a missing key reports as absent rather than as a level', () => {
  const gaps = permissionGap(DECLARED, { contents: 'read', checks: 'write' });
  assert.deepEqual(gaps, [['issues', 'write', 'absent']]);
});

test('an installation that matches has no gap', () => {
  assert.deepEqual(permissionGap(DECLARED, { ...DECLARED }), []);
});

test('holding more than the app declares is its own finding', () => {
  assert.deepEqual(
    permissionSurplus(DECLARED, { contents: 'write', issues: 'write', checks: 'write' }),
    [['contents', 'read', 'write']],
  );
  assert.deepEqual(
    permissionSurplus(DECLARED, { ...DECLARED, members: 'read' }),
    [['members', 'not declared', 'read']],
  );
});

test('events are compared case and space insensitively', () => {
  assert.deepEqual(eventGap(EVENTS, [' Push ', 'issues', 'check_run']), []);
  assert.deepEqual(eventGap(EVENTS, ['push']), ['check_run', 'issues']);
});

test('an installation behind on anything is upgrade pending', () => {
  const row = classify(DECLARED, EVENTS, install(1, 'beta-inc', { contents: 'read', checks: 'write' }));
  assert.equal(row.state, 'upgrade-pending');
  assert.equal(row.account, 'beta-inc');
  assert.deepEqual(row.permission_gap, [['issues', 'write', 'absent']]);
});

test('an installation behind only on events is still pending', () => {
  const row = classify(DECLARED, EVENTS, install(2, 'acme', { ...DECLARED }, ['push']));
  assert.equal(row.state, 'upgrade-pending');
  assert.deepEqual(row.event_gap, ['check_run', 'issues']);
});

test('an installation that agrees is current', () => {
  assert.equal(classify(DECLARED, EVENTS, install(3, 'acme', { ...DECLARED })).state, 'current');
});

test('the verdict reports pending before anything else', () => {
  const rows = [
    classify(DECLARED, EVENTS, install(1, 'a', { ...DECLARED })),
    classify(DECLARED, EVENTS, install(2, 'b', { contents: 'read' })),
  ];
  const [state, detail] = verdict(rows);
  assert.equal(state, 'upgrades-pending');
  assert.match(detail, /1 of 2/);
});

test('a fleet that is only ahead is not an outage', () => {
  const rows = [classify(DECLARED, EVENTS, install(1, 'a', { ...DECLARED, contents: 'write' }))];
  const [state, detail] = verdict(rows);
  assert.equal(state, 'grants-ahead');
  assert.match(detail, /Nothing is failing/);
});

test('an app with no installations says so rather than all current', () => {
  assert.equal(verdict([])[0], 'no-installations');
});

test('all current when every map agrees', () => {
  const rows = [0, 1, 2].map((i) => classify(DECLARED, EVENTS, install(i, String(i), { ...DECLARED })));
  assert.equal(verdict(rows)[0], 'all-current');
});

test('accounts missing the same thing collapse into one cohort', () => {
  const rows = [
    classify(DECLARED, EVENTS, install(1, 'beta', { contents: 'read', checks: 'write' })),
    classify(DECLARED, EVENTS, install(2, 'gamma', { contents: 'read', checks: 'write' })),
    classify(DECLARED, EVENTS, install(3, 'delta', { ...DECLARED })),
  ];
  const grouped = cohorts(rows);
  assert.equal(Object.keys(grouped).length, 1);
  assert.deepEqual(Object.values(grouped)[0], ['beta', 'gamma']);
});
