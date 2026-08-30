import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  coverage, pollCost, subscribedEvents, verdict,
} from './github-webhook-vs-poll.mjs';

const ACTIVE = [{ id: 1, active: true, events: ['issues', 'issue_comment'] }];
const DISABLED = [{ id: 2, active: false, events: ['issues'] }];
const WILDCARD = [{ id: 3, active: true, events: ['*'] }];

test('active and inactive subscriptions are kept apart', () => {
  const subs = subscribedEvents([...ACTIVE, ...DISABLED]);
  assert.ok(subs.events.has('issue_comment'));
  assert.deepEqual([...subs.inactive], ['issues']);
  assert.equal(subs.wildcard, false);
});

test('a wildcard is recognised only when the hook is active', () => {
  assert.equal(subscribedEvents(WILDCARD).wildcard, true);
  const off = [{ id: 4, active: false, events: ['*'] }];
  assert.equal(subscribedEvents(off).wildcard, false);
  assert.equal(subscribedEvents(off).inactive_wildcard, true);
});

test('junk in the hook list does not throw', () => {
  assert.equal(subscribedEvents([null, 'nope', {}]).events.size, 0);
  assert.equal(subscribedEvents(null).events.size, 0);
});

test('an active hook covers its concern', () => {
  const rows = coverage(['issues', 'pulls'], ACTIVE);
  assert.equal(rows[0].state, 'covered');
  assert.equal(rows[1].state, 'uncovered');
});

test('a disabled hook is uncovered and says why', () => {
  const rows = coverage(['issues'], DISABLED);
  assert.equal(rows[0].state, 'uncovered');
  assert.match(rows[0].detail, /not active/);
});

test('a wildcard covers everything and warns that it does', () => {
  const rows = coverage(['issues', 'commits', 'releases'], WILDCARD);
  assert.deepEqual(rows.map((r) => r.state), ['covered', 'covered', 'covered']);
  assert.match(rows[0].detail, /everything else/);
});

test('an unknown concern is matched against its own name', () => {
  const rows = coverage(['deployment'], [{ active: true, events: ['deployment'] }]);
  assert.equal(rows[0].state, 'covered');
});

test('no hooks at all leaves every concern uncovered', () => {
  const rows = coverage(['issues', 'pulls'], []);
  assert.ok(rows.every((r) => r.state === 'uncovered'));
  assert.match(rows[0].detail, /no hook subscribes/);
});

test('the poll costs endpoints times repos times the clock', () => {
  const cost = pollCost(['issues', 'pulls'], 60, 3);
  assert.equal(cost.requests_per_hour, 360);
  assert.equal(cost.requests_per_day, 8640);
});

test('latency is half the interval on average and all of it at worst', () => {
  const cost = pollCost(['issues'], 60);
  assert.equal(cost.mean_latency_s, 30);
  assert.equal(cost.worst_latency_s, 60);
});

test('a zero interval is clamped rather than dividing by zero', () => {
  assert.equal(pollCost(['issues'], 0).requests_per_hour, 3600);
});

test('an uncovered concern is reported with both numbers', () => {
  const rows = coverage(['issues', 'pulls'], []);
  const [state, detail] = verdict(rows, pollCost(['issues', 'pulls'], 60, 3));
  assert.equal(state, 'polling');
  assert.match(detail, /2 of 2/);
  assert.match(detail, /360 request\(s\)/);
  assert.match(detail, /30s late/);
});

test('a loop spending half the quota is called out as such', () => {
  const rows = coverage(['issues', 'pulls'], []);
  const [state, detail] = verdict(rows, pollCost(['issues', 'pulls'], 1, 1));
  assert.equal(state, 'polling-dominates');
  assert.match(detail, /%/);
});

test('full coverage reframes the loop as reconciliation', () => {
  const [state, detail] = verdict(coverage(['issues'], ACTIVE), pollCost(['issues'], 3600));
  assert.equal(state, 'push');
  assert.match(detail, /reconciliation/);
});

test('polling nothing is its own state', () => {
  assert.equal(verdict([], pollCost([], 60))[0], 'nothing-polled');
});
