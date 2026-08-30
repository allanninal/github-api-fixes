import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  gatingPermission, holds, normalize, repairSteps, rows, seenEvents,
  subscriptionState, verdict,
} from './github-app-event-subscriptions.mjs';

const SUBSCRIBED = ['push', 'issues'];
const PERMISSIONS = { contents: 'read', issues: 'write', metadata: 'read' };

test('names are normalised for case and space only', () => {
  assert.equal(normalize('  Pull_Request '), 'pull_request');
  assert.equal(normalize('pull-request'), 'pull-request');
});

test('a misspelled event stays unknown rather than being corrected', () => {
  assert.equal(gatingPermission('pull-request'), null);
  assert.equal(gatingPermission('pull_request'), 'pull_requests');
});

test('metadata counts as held even when absent from the map', () => {
  assert.ok(holds({}, 'metadata'));
  assert.ok(!holds({}, 'checks'));
  assert.ok(!holds({ checks: 'none' }, 'checks'));
  assert.ok(holds({ checks: 'read' }, 'checks'));
});

test('an unsubscribed event without its permission is blocked', () => {
  const [state, detail] = subscriptionState('pull_request_review_thread', SUBSCRIBED, PERMISSIONS);
  assert.equal(state, 'not-subscribed-blocked');
  assert.match(detail, /pull_requests permission/);
  assert.match(detail, /cannot be ticked/);
});

test('an unsubscribed event whose permission is held is a lighter repair', () => {
  const [state, detail] = subscriptionState('release', SUBSCRIBED, PERMISSIONS);
  assert.equal(state, 'not-subscribed-permitted');
  assert.match(detail, /contents permission/);
});

test('an unknown event gets a subscription answer and no permission guess', () => {
  const [state, detail] = subscriptionState('sponsorship_tier_change', SUBSCRIBED, PERMISSIONS);
  assert.equal(state, 'not-subscribed-gate-unknown');
  assert.match(detail, /does not know which permission/);
});

test('a subscribed event seen in the log is healthy', () => {
  const seen = seenEvents([{ event: 'push' }, { event: 'Push' }, { nope: 1 }]);
  assert.deepEqual([...seen], ['push']);
  assert.equal(subscriptionState('push', SUBSCRIBED, PERMISSIONS, seen)[0],
    'subscribed-and-arriving');
});

test('silence in the delivery log is never a finding on its own', () => {
  const [state, detail] = subscriptionState('issues', SUBSCRIBED, PERMISSIONS, new Set());
  assert.equal(state, 'subscribed-not-yet-seen');
  assert.match(detail, /rather than that it is broken/);
});

test('any unsubscribed handler makes the whole report unreachable', () => {
  const report = rows(['push', 'release'], SUBSCRIBED, PERMISSIONS, new Set(['push']));
  const [state, detail] = verdict(report);
  assert.equal(state, 'handlers-unreachable');
  assert.match(detail, /1 of 2/);
});

test('a fully subscribed quiet app is not reported as broken', () => {
  const report = rows(['push', 'issues'], SUBSCRIBED, PERMISSIONS, new Set(['push']));
  assert.equal(verdict(report)[0], 'all-subscribed-some-quiet');
});

test('a fully subscribed busy app is clean', () => {
  const report = rows(['push', 'issues'], SUBSCRIBED, PERMISSIONS, new Set(['push', 'issues']));
  assert.equal(verdict(report)[0], 'all-subscribed');
});

test('no handled events is not a pass', () => {
  assert.equal(verdict([])[0], 'nothing-handled');
});

test('the repair puts the permission before the subscription', () => {
  const steps = repairSteps(rows(['pull_request_review_thread'], SUBSCRIBED, PERMISSIONS));
  assert.equal(steps.length, 3);
  assert.match(steps[0], /add the pull_requests permission/);
  assert.match(steps[1], /subscribe the App to pull_request_review_thread/);
  assert.match(steps[2], /accept/);
});

test('the permission step is skipped when the permission is already held', () => {
  const steps = repairSteps(rows(['release'], SUBSCRIBED, PERMISSIONS));
  assert.equal(steps.length, 2);
  assert.ok(steps[0].startsWith('subscribe the App to release'));
});

test('a clean report has no repair', () => {
  assert.deepEqual(repairSteps(rows(['push'], SUBSCRIBED, PERMISSIONS, new Set(['push']))), []);
});
