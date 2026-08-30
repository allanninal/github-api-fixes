import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  byEvent, classify, durationMs, nextLink, percentile, repair,
  slowestEvent, stats, timedOut, verdict,
} from './github-hook-delivery-duration.mjs';

const d = (duration, event = 'push', status = 'OK') => ({ duration, event, status });

test('seconds and milliseconds both normalise to milliseconds', () => {
  assert.equal(durationMs(d(0.62)), 620);
  assert.equal(durationMs(d(9.87)), 9870);
  assert.equal(durationMs(d(9870)), 9870);
  assert.equal(durationMs(d(60)), 60000);
  assert.equal(durationMs(d(61)), 61);
});

test('an unreadable duration is null rather than zero', () => {
  assert.equal(durationMs(d(null)), null);
  assert.equal(durationMs(d('slow')), null);
  assert.equal(durationMs(d(true)), null);
  assert.equal(durationMs(d(-1)), null);
  assert.equal(durationMs(null), null);
});

test('an abandoned delivery is recognised from either column', () => {
  assert.ok(timedOut({ status: 'timed out', duration: null }));
  assert.ok(timedOut({ status: 'Timed Out', duration: 2.0 }));
  assert.ok(timedOut({ status: '', duration: 10.0 }));
  assert.ok(!timedOut(d(1.0)));
  assert.ok(!timedOut(null));
});

test('each delivery is sorted by the room it had left', () => {
  assert.equal(classify(d(9.5)), 'at-risk');
  assert.equal(classify(d(6.0)), 'slow');
  assert.equal(classify(d(0.4)), 'fine');
  assert.equal(classify(d(10.0)), 'timed-out');
  assert.equal(classify(d(null)), 'unknown');
});

test('the percentile is nearest rank and never invents a value', () => {
  const values = [100, 200, 300, 400];
  assert.equal(percentile(values, 50), 200);
  assert.equal(percentile(values, 95), 400);
  assert.equal(percentile(values, 0), 100);
  assert.equal(percentile([], 95), null);
  assert.equal(percentile([7], 95), 7);
});

test('a window with no failures and a nine second tail is a finding', () => {
  const rows = [...Array(18).fill(d(0.5)), ...Array(2).fill(d(9.1))];
  const st = stats(rows);
  assert.equal(st.timed_out, 0);
  const [state, detail] = verdict(st);
  assert.equal(state, 'at-the-edge');
  assert.match(detail, /fails on the next slow week/);
  assert.match(repair(state), /return 202/);
});

test('a fast receiver is left alone', () => {
  assert.equal(verdict(stats(Array(50).fill(d(0.2))))[0], 'healthy');
  assert.ok(repair('healthy').startsWith('nothing'));
});

test('timeouts are reported with the headroom on everything else', () => {
  const rows = [
    ...Array(90).fill(d(0.5)),
    ...Array(10).fill({ status: 'timed out', event: 'push' }),
  ];
  const st = stats(rows);
  assert.equal(st.timed_out, 10);
  const [state, detail] = verdict(st);
  assert.equal(state, 'timing-out');
  assert.match(detail, /10 deliveries were abandoned/);
});

test('an empty window is never reported as healthy', () => {
  const [state, detail] = verdict(stats([]));
  assert.equal(state, 'no-data');
  assert.match(detail, /not the same as a receiver that is fast/);
});

test('a window with statuses but no timings says so', () => {
  const rows = Array(5).fill({ event: 'push', status: 'OK' });
  assert.equal(verdict(stats(rows))[0], 'no-durations');
});

test('the grouping finds the handler to fix first', () => {
  const rows = [...Array(5).fill(d(9.4, 'push')), ...Array(5).fill(d(0.3, 'issues'))];
  const worst = slowestEvent(rows);
  assert.equal(worst.event, 'push');
  assert.equal(worst.p95, 9400);
  assert.match(repair('slow', worst), /Start with push/);
});

test('a rare event is kept when it timed out', () => {
  const rows = [
    ...Array(5).fill(d(0.2, 'issues')),
    { event: 'release', status: 'timed out' },
  ];
  const grouped = byEvent(rows);
  assert.ok('release' in grouped);
  assert.equal(grouped.release.timed_out, 1);
});

test('the cursor is read from the Link header', () => {
  const header = '<https://api.github.com/repos/a/b/hooks/1/deliveries?cursor=v2>; rel="next"';
  assert.ok(nextLink({ Link: header }).endsWith('cursor=v2'));
  assert.equal(nextLink({ Link: '<https://x>; rel="prev"' }), null);
  assert.equal(nextLink({}), null);
});
