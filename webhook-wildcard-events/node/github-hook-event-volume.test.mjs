import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  handledSet, isWildcard, neverSeen, nextLink, normalize,
  proposedEvents, repair, subscribed, tally, verdict, waste,
} from './github-hook-event-volume.mjs';

const HANDLES = 'issues,pull_request,release';
const STAR = { id: 1, events: ['*'], config: { url: 'https://hooks.example.com/gh' } };
const TIGHT = { id: 2, events: ['Issues', ' pull_request '], config: {} };

test('event names are normalised narrowly', () => {
  assert.equal(normalize(' Pull_Request '), 'pull_request');
  assert.equal(normalize(null), '');
  assert.deepEqual(subscribed(TIGHT), ['issues', 'pull_request']);
  assert.deepEqual(subscribed({ events: 'issues' }), []);
  assert.deepEqual(subscribed(null), []);
});

test('the wildcard is recognised however it is written', () => {
  assert.ok(isWildcard(['*']));
  assert.ok(isWildcard(['push', ' * ']));
  assert.ok(!isWildcard(['push']));
  assert.ok(!isWildcard([]));
});

test('the handled set drops a wildcard it is given', () => {
  assert.deepEqual([...handledSet('issues, *, push')].sort(), ['issues', 'push']);
  assert.deepEqual([...handledSet(['Issues', 'issues'])], ['issues']);
  assert.equal(handledSet('').size, 0);
});

test('the tally counts by event and names the unknown', () => {
  const rows = [{ event: 'push' }, { event: 'Push' }, { event: null }, 'junk'];
  assert.deepEqual(tally(rows), { push: 2, unknown: 1 });
});

test('the waste is the fraction the receiver discards', () => {
  const counts = { push: 300, status: 110, issues: 90 };
  const w = waste(counts, HANDLES);
  assert.equal(w.total, 500);
  assert.equal(w.unhandled_deliveries, 410);
  assert.equal(w.share, 82.0);
  assert.deepEqual(w.unhandled_events, ['push', 'status']);
});

test('an empty window does not divide by zero', () => {
  assert.deepEqual(waste({}, HANDLES), {
    total: 0, unhandled_deliveries: 0, unhandled_events: [], share: 0,
  });
});

test('a wildcard with wasted volume is the headline finding', () => {
  const counts = { push: 300, status: 110, issues: 90 };
  const [state, detail] = verdict(subscribed(STAR), counts, HANDLES);
  assert.equal(state, 'wildcard');
  assert.match(detail, /82\.0%/);
  assert.match(detail, /ships next/);
});

test('a wildcard stays a finding when the window was all wanted', () => {
  const [state, detail] = verdict(['*'], { issues: 12 }, HANDLES);
  assert.equal(state, 'wildcard-all-handled');
  assert.match(detail, /luck rather than design/);
});

test('a wildcard with no deliveries is still reported', () => {
  const [state, detail] = verdict(['*'], {}, HANDLES);
  assert.equal(state, 'wildcard-unmeasured');
  assert.match(detail, /open ended/);
});

test('events the receiver handles and the hook omits are not this finding', () => {
  const [state] = verdict(['issues', 'pull_request'], { issues: 4 }, HANDLES);
  assert.equal(state, 'tight');
});

test('events on the hook and not in the code are this finding', () => {
  const [state, detail] = verdict(['issues', 'push', 'status'], { push: 3 }, HANDLES);
  assert.equal(state, 'over-subscribed');
  assert.match(detail, /push, status/);
});

test('an empty subscription is its own state', () => {
  assert.equal(verdict([], {}, HANDLES)[0], 'no-events');
});

test('the proposal keeps a handled event that never fired', () => {
  assert.deepEqual(proposedEvents(HANDLES), ['issues', 'pull_request', 'release']);
  assert.deepEqual(neverSeen({ issues: 3 }, HANDLES), ['pull_request', 'release']);
  const text = repair('wildcard', HANDLES, { issues: 3 });
  assert.ok(text.includes('["issues","pull_request","release"]'));
  assert.match(text, /Keep pull_request, release on the list/);
});

test('a tight hook gets no repair', () => {
  assert.ok(repair('tight', HANDLES).startsWith('nothing'));
});

test('the cursor is read from the Link header', () => {
  const header = '<https://api.github.com/repos/a/b/hooks/1/deliveries?cursor=v2>; rel="next"';
  assert.ok(nextLink({ Link: header }).endsWith('cursor=v2'));
  assert.equal(nextLink({}), null);
});
