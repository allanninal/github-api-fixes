import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  pointsFor, costProfile, safeRate, verdict,
} from './github-endpoint-cost-audit.mjs';

test('reads cost one point and writes cost five', () => {
  assert.equal(pointsFor('GET'), 1);
  assert.equal(pointsFor('head'), 1);
  assert.equal(pointsFor('OPTIONS'), 1);
  assert.equal(pointsFor('patch'), 5);
  assert.equal(pointsFor('delete'), 5);
});

test('an unknown method is charged the expensive rate', () => {
  assert.equal(pointsFor('QUERY'), 5);
  assert.equal(pointsFor(null), 5);
  assert.equal(pointsFor(''), 5);
});

test('samples are grouped by path and averaged', () => {
  const profile = costProfile([
    { path: '/a', method: 'GET', seconds: 0.1 },
    { path: '/a', method: 'GET', seconds: 0.3 },
    { path: '/b', method: 'GET', seconds: 1.0 },
  ]);
  assert.equal(profile['/a'].calls, 2);
  assert.equal(profile['/a'].mean_seconds, 0.2);
  assert.equal(profile['/a'].max_seconds, 0.3);
  assert.equal(profile['/b'].mean_seconds, 1);
});

test('a malformed sample is dropped rather than counted as instant', () => {
  const profile = costProfile([
    { path: '/a', seconds: 0.5 },
    { path: '/a', seconds: 'slow' },
    { seconds: 0.5 },
    { path: '/a', seconds: -1 },
  ]);
  assert.equal(profile['/a'].calls, 1);
  assert.equal(profile['/a'].mean_seconds, 0.5);
});

test('no samples profile nothing', () => {
  assert.deepEqual(costProfile([]), {});
  assert.deepEqual(costProfile(null), {});
});

test('a fast endpoint is bound by points', () => {
  const safe = safeRate(0.04);
  assert.equal(safe.binding, 'points');
  assert.equal(safe.per_minute, 900);
  assert.equal(safe.by_cpu, 2250);
});

test('a slow endpoint is bound by CPU time instead', () => {
  const safe = safeRate(0.6);
  assert.equal(safe.binding, 'cpu');
  assert.equal(safe.per_minute, 150);
});

test('the two ceilings cross at a tenth of a second', () => {
  assert.equal(safeRate(0.09).binding, 'points');
  assert.equal(safeRate(0.11).binding, 'cpu');
});

test('a very expensive endpoint collapses to a handful a minute', () => {
  assert.equal(safeRate(3).per_minute, 30);
});

test('a write costs five points so its ceiling is a fifth', () => {
  assert.equal(safeRate(0.01, 5).per_minute, 180);
});

test('a zero response time does not divide by zero', () => {
  const safe = safeRate(0);
  assert.equal(safe.by_cpu, null);
  assert.equal(safe.binding, 'points');
  assert.equal(safeRate('unmeasured').per_minute, 900);
});

test('with no configured rate the ceiling is simply reported', () => {
  const [state, detail] = verdict('/x', {}, safeRate(0.5));
  assert.equal(state, 'ceiling');
  assert.match(detail, /180/);
});

test('a rate above the ceiling names the cap that binds', () => {
  const [state, detail] = verdict('/x', { max_seconds: 0.9 }, safeRate(0.6), 400);
  assert.equal(state, 'over-budget');
  assert.match(detail, /CPU/);
});

test('a rate just under the ceiling is not reported as fine', () => {
  const [state, detail] = verdict('/x', { max_seconds: 0.9 }, safeRate(0.6), 130);
  assert.equal(state, 'near-budget');
  assert.match(detail, /0\.900 s/);
});

test('an expensive path is flagged even at a low rate', () => {
  const [state, detail] = verdict('/x', { max_seconds: 2.4 }, safeRate(2), 5);
  assert.equal(state, 'expensive');
  assert.match(detail, /move work off/);
});

test('a cheap path at a modest rate is clear', () => {
  const [state, detail] = verdict('/x', { max_seconds: 0.05 }, safeRate(0.04), 60);
  assert.equal(state, 'clear');
  assert.match(detail, /900-points/);
});
