import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  accountOf, daysSince, find, isSuspended, repair, retryable,
  summarize, suspendedAt, suspendedBy, verdict,
} from './github-installation-suspension.mjs';

const NOW = Date.parse('2026-08-30T12:00:00Z');
const LIVE = { id: 41234567, account: { login: 'acme-corp' }, suspended_at: null };
const DEAD = {
  id: 41234568,
  account: { login: 'beta-inc' },
  suspended_at: '2026-08-27T09:14:22Z',
  suspended_by: { login: 'octo-admin' },
};

test('every shape of absent timestamp means not suspended', () => {
  assert.ok(!isSuspended({ id: 1 }));
  assert.ok(!isSuspended({ id: 1, suspended_at: null }));
  assert.ok(!isSuspended({ id: 1, suspended_at: '' }));
  assert.ok(!isSuspended({ id: 1, suspended_at: '   ' }));
  assert.ok(!isSuspended({ id: 1, suspended_at: 'null' }));
});

test('a real timestamp is a suspension', () => {
  assert.ok(isSuspended(DEAD));
  assert.equal(suspendedAt(DEAD), '2026-08-27T09:14:22Z');
});

test('a suspension with no named actor is still a suspension', () => {
  const anon = { id: 9, suspended_at: '2026-08-27T09:14:22Z', suspended_by: null };
  assert.ok(isSuspended(anon));
  assert.equal(suspendedBy(anon), null);
  assert.equal(suspendedBy(DEAD), 'octo-admin');
});

test('the age is measured from the timestamp', () => {
  assert.equal(daysSince('2026-08-27T09:14:22Z', NOW), 3);
  assert.equal(daysSince('not a date', NOW), null);
  assert.equal(daysSince(null, NOW), null);
});

test('an id matches whether it was stored as text or a number', () => {
  assert.equal(find([LIVE, DEAD], 41234568), DEAD);
  assert.equal(find([LIVE, DEAD], '41234568'), DEAD);
  assert.equal(find([LIVE, DEAD], ' 41234568 '), DEAD);
  assert.equal(find([LIVE, DEAD], 999), null);
});

test('the summary counts both sides', () => {
  assert.deepEqual(summarize([LIVE, DEAD, { id: 3 }]), {
    total: 3, suspended: 1, active: 2, suspended_ids: [41234568],
  });
});

test('a suspended installation names the moment and the actor', () => {
  const [state, detail] = verdict(DEAD, null, NOW);
  assert.equal(state, 'suspended');
  assert.match(detail, /octo-admin/);
  assert.match(detail, /3 day\(s\) ago/);
  assert.ok(!retryable(state));
});

test('a missing id is never reported as a suspension', () => {
  const [state, detail] = verdict(null, 403, NOW);
  assert.equal(state, 'not-listed');
  assert.match(detail, /different repair/);
  assert.ok(!retryable(state));
});

test('a 403 on an active installation is sent elsewhere', () => {
  const [state, detail] = verdict(LIVE, 403, NOW);
  assert.equal(state, 'active-but-refused');
  assert.match(detail, /rather than about suspension/);
  assert.ok(retryable(state));
});

test('an active installation with no probe is just active', () => {
  assert.equal(verdict(LIVE, null, NOW)[0], 'active');
  assert.ok(retryable('active'));
});

test('the repair for a suspension names the account and forbids retrying', () => {
  const text = repair('suspended', DEAD);
  assert.match(text, /beta-inc/);
  assert.match(text, /Retrying cannot help/);
});

test('the account falls back rather than throwing', () => {
  assert.equal(accountOf({ id: 1 }), 'an unnamed account');
  assert.equal(accountOf(null), 'an unnamed account');
});
