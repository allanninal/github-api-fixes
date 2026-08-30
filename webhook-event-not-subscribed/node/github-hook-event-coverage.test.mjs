import { test } from 'node:test';
import assert from 'node:assert/strict';
import { coverage, normalize } from './github-hook-event-coverage.mjs';

const byEvent = (rows) => Object.fromEntries(rows.map((r) => [r.event, r]));

test('normalize accepts the three spellings people use', () => {
  assert.equal(normalize('pull_request'), 'pull_request');
  assert.equal(normalize('pull-request'), 'pull_request');
  assert.equal(normalize('Pull_Request.opened'), 'pull_request');
  assert.equal(normalize(null), '');
});

test('an unsubscribed handler is the finding', () => {
  const rows = byEvent(coverage(['release'], ['push', 'pull_request'], ['push']));
  assert.equal(rows.release.state, 'missing');
});

test('an action suffix matches the event it belongs to', () => {
  const rows = byEvent(coverage(['pull_request.opened'], ['pull_request'],
    ['pull_request']));
  assert.equal(rows.pull_request.state, 'delivered');
  assert.match(rows.pull_request.note, /GitHub spells this/);
});

test('subscribed but unseen is not the same as unsubscribed', () => {
  const rows = byEvent(coverage(['release'], ['release', 'push'], ['push']));
  assert.equal(rows.release.state, 'quiet');
});

test('a wildcard is reported rather than counted as success', () => {
  const rows = byEvent(coverage(['release'], ['*'], ['push']));
  assert.equal(rows.release.state, 'wildcard');
});

test('traffic nothing handles is reported too', () => {
  const rows = byEvent(coverage(['push'], ['push', 'status'],
    ['push', 'status', 'status']));
  assert.equal(rows.status.state, 'unhandled');
  assert.equal(rows.status.seen, 2);
  assert.equal(rows.push.state, 'delivered');
});

test('an event arriving without a subscription is still surfaced', () => {
  const rows = byEvent(coverage(['push'], ['push'], ['push', 'ping']));
  assert.equal(rows.ping.state, 'unhandled');
  assert.match(rows.ping.note, /without a subscription/);
});

test('case and hyphens do not create phantom findings', () => {
  const rows = coverage(['Pull-Request'], ['pull_request'], ['pull_request']);
  assert.deepEqual(rows.map((r) => r.state), ['delivered']);
});
