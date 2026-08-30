import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  activeState, classify, daysSince, editedAfterCreation, failedLast,
  lastCode, newestDelivery, repair, silentDays, summarize,
} from './github-hook-active-audit.mjs';

const NOW = Date.parse('2026-08-30T12:00:00Z');

const FRESH = {
  id: 1, active: true,
  created_at: '2026-01-04T10:00:00Z', updated_at: '2026-01-04T10:00:01Z',
};
const TOGGLED = {
  id: 2, active: false,
  created_at: '2026-01-04T10:00:00Z', updated_at: '2026-07-26T02:11:00Z',
};
const BORN_OFF = {
  id: 3, active: false,
  created_at: '2026-01-04T10:00:00Z', updated_at: '2026-01-04T10:00:02Z',
};
const DISABLED = {
  id: 4, active: false,
  created_at: '2026-01-04T10:00:00Z', updated_at: '2026-07-26T02:11:00Z',
  last_response: { code: 502, status: 'bad gateway' },
};

test('a truthy test would get the string false wrong', () => {
  assert.equal(activeState({ active: 'false' }), 'off');
  assert.equal(activeState({ active: '0' }), 'off');
  assert.equal(activeState({ active: 0 }), 'off');
  assert.equal(activeState({ active: 'true' }), 'on');
  assert.equal(activeState({ active: 1 }), 'on');
});

test('an absent flag is unknown and not off', () => {
  assert.equal(activeState({ id: 1 }), 'unknown');
  assert.equal(activeState({ active: null }), 'unknown');
  assert.equal(activeState({ active: 'maybe' }), 'unknown');
  assert.equal(activeState(null), 'unknown');
});

test('the last response code survives every shape it arrives in', () => {
  assert.equal(lastCode(DISABLED), 502);
  assert.equal(lastCode({ last_response: { code: '500' } }), 500);
  assert.equal(lastCode({ last_response: { code: null } }), null);
  assert.equal(lastCode({ last_response: {} }), null);
  assert.equal(lastCode({ id: 1 }), null);
  assert.ok(failedLast(DISABLED));
  assert.ok(!failedLast({ last_response: { code: 200 } }));
});

test('a hook configured in one call is not called edited', () => {
  assert.equal(editedAfterCreation(BORN_OFF), false);
  assert.equal(editedAfterCreation(TOGGLED), true);
  assert.equal(editedAfterCreation({ created_at: '2026-01-04T10:00:00Z' }), null);
});

test('the three routes to off are three different states', () => {
  assert.equal(classify(DISABLED, null, NOW)[0], 'inactive-after-failures');
  assert.equal(classify(TOGGLED, null, NOW)[0], 'inactive-toggled');
  assert.equal(classify(BORN_OFF, null, NOW)[0], 'inactive-since-creation');
});

test('a disabled hook is never reported as a plain toggle', () => {
  const [state, detail] = classify(DISABLED, null, NOW);
  assert.equal(state, 'inactive-after-failures');
  assert.match(detail, /502/);
  assert.match(detail, /aftermath/);
});

test('an off hook with no timestamps says so rather than guessing', () => {
  const [state, detail] = classify({ id: 9, active: false }, null, NOW);
  assert.equal(state, 'inactive-undated');
  assert.match(detail, /cannot be told from here/);
});

test('an on hook with an empty log is sent to a different question', () => {
  const [state, detail] = classify(FRESH, [], NOW);
  assert.equal(state, 'active-but-silent');
  assert.match(detail, /not the problem/);
  assert.match(repair(state, FRESH), /events array/);
});

test('an on hook with a recent delivery is simply active', () => {
  const log = [{ delivered_at: '2026-08-30T09:00:00Z', status: 'OK' }];
  assert.equal(classify(FRESH, log, NOW)[0], 'active');
});

test('the delivery log is read for its newest row not its first', () => {
  const log = [
    { delivered_at: '2026-08-01T09:00:00Z' },
    { delivered_at: '2026-08-29T09:00:00Z' },
    { delivered_at: 'not a date' },
    'junk',
  ];
  assert.equal(newestDelivery(log), '2026-08-29T09:00:00Z');
  assert.equal(silentDays(log, NOW), 1);
  assert.equal(newestDelivery([]), null);
  assert.equal(silentDays([], NOW), null);
});

test('the repair for a disabled hook puts the receiver first', () => {
  const text = repair('inactive-after-failures', DISABLED, 'acme-corp/api');
  assert.ok(text.indexOf('fix the receiver') < text.indexOf('re-enable'));
  assert.match(text, /\/repos\/acme-corp\/api\/hooks\/4/);
});

test('the summary counts the hooks that are off', () => {
  const stats = summarize([FRESH, TOGGLED, DISABLED, { id: 5 }]);
  assert.equal(stats.total, 4);
  assert.equal(stats.inactive, 2);
  assert.equal(stats.active, 1);
  assert.deepEqual(stats.inactive_ids, [2, 4]);
});

test('daysSince refuses to invent an age', () => {
  assert.equal(daysSince('2026-08-27T12:00:00Z', NOW), 3);
  assert.equal(daysSince('', NOW), null);
  assert.equal(daysSince('null', NOW), null);
});
