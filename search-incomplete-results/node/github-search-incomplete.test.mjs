import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  RESULT_CAP, SEARCH_BUCKET, aboveResultCap, cacheable, countsStable, flagged,
  itemCount, maxTotal, narrowing, observe, qualifiers, readCost, repair,
  retryOrNarrow, summarise, totalOf, verdict, withinSearchBucket,
} from './github-search-incomplete.mjs';

const PARTIAL = { total_count: 412, incomplete_results: true, items: [{ id: 1 }, { id: 2 }] };
const WHOLE = {
  total_count: 412, incomplete_results: false, items: [{ id: 1 }, { id: 2 }, { id: 3 }],
};

test('the flag is read strictly rather than truthily', () => {
  assert.ok(flagged(PARTIAL));
  assert.ok(!flagged(WHOLE));
  assert.ok(!flagged({ incomplete_results: 'true' }));
  assert.ok(!flagged({ incomplete_results: 1 }));
  assert.ok(!flagged({}));
  assert.ok(!flagged(null));
});

test('the three kept fields survive a malformed payload', () => {
  assert.deepEqual(observe(PARTIAL), { incomplete: true, total: 412, items: 2 });
  assert.equal(totalOf({ total_count: '412' }), 412);
  assert.equal(totalOf({ total_count: null }), null);
  assert.equal(itemCount({ items: null }), 0);
  assert.equal(itemCount(null), 0);
});

test('a flagged response may never be cached', () => {
  assert.ok(!cacheable(PARTIAL));
  assert.ok(cacheable(WHOLE));
  assert.ok(!cacheable(null));
});

test('every round partial is narrowed, not retried', () => {
  const obs = [observe(PARTIAL), observe(PARTIAL), observe(PARTIAL)];
  const [state, detail] = verdict(obs);
  assert.equal(state, 'timed-out-always');
  assert.match(detail, /No retry policy will fix that/);
  assert.equal(retryOrNarrow(obs), 'narrow');
});

test('some rounds partial is retried, not narrowed', () => {
  const obs = [observe(PARTIAL), observe(WHOLE), observe(WHOLE)];
  const [state, detail] = verdict(obs);
  assert.equal(state, 'timed-out-intermittent');
  assert.match(detail, /1 of 3/);
  assert.equal(retryOrNarrow(obs), 'retry');
});

test('the thousand-result ceiling is ruled out by name', () => {
  const detail = verdict([observe(PARTIAL), observe(PARTIAL)])[1];
  assert.match(detail, /1000-result ceiling/);
  assert.match(detail, /not the explanation/);
});

test('a query over the ceiling is reported as two problems', () => {
  const big = { ...PARTIAL, total_count: 24831 };
  const obs = [observe(big), observe(big)];
  const [state, detail] = verdict(obs);
  assert.equal(state, 'timed-out-and-capped');
  assert.match(detail, /two separate problems/);
  assert.equal(retryOrNarrow(obs), 'narrow');
});

test('a moving count with no flag is still caught', () => {
  const obs = [observe(WHOLE), observe({ ...WHOLE, items: [{ id: 1 }] })];
  const [state, detail] = verdict(obs);
  assert.equal(state, 'unstable-counts');
  assert.match(detail, /no round was flagged/);
  assert.equal(retryOrNarrow(obs), 'retry');
});

test('three clean stable rounds are not a finding', () => {
  const obs = [observe(WHOLE), observe(WHOLE), observe(WHOLE)];
  assert.equal(verdict(obs)[0], 'complete');
  assert.equal(retryOrNarrow(obs), 'nothing');
  assert.ok(countsStable(obs));
});

test('no rounds is not reported as a clean result', () => {
  assert.equal(verdict([])[0], 'no-observations');
  assert.deepEqual(summarise([]), {
    rounds: 0, flagged: 0, item_counts: [], totals: [],
  });
  assert.equal(maxTotal([]), null);
});

test('the ceiling predicate is strictly above the cap', () => {
  assert.ok(aboveResultCap(RESULT_CAP + 1));
  assert.ok(!aboveResultCap(RESULT_CAP));
  assert.ok(!aboveResultCap(null));
});

test('the query is read for the qualifiers it already has', () => {
  assert.deepEqual([...qualifiers('is:issue repo:acme/api label:bug')].sort(),
    ['is', 'label', 'repo']);
  assert.deepEqual([...qualifiers('-org:acme is:open')].sort(), ['is', 'org']);
  assert.equal(qualifiers('').size, 0);
  assert.equal(qualifiers(null).size, 0);
});

test('narrowing suggests only what is missing', () => {
  assert.deepEqual(narrowing('is:issue state:open'),
    ['repo: or org:', 'created: or updated: date range', 'language:']);
  assert.deepEqual(narrowing('org:acme created:>2026-01-01 language:go'), []);
  assert.deepEqual(narrowing('repo:acme/api updated:>2026-01-01'), ['language:']);
});

test('the repair tells a hopeless query not to retry', () => {
  const fix = repair('timed-out-always', 'is:issue state:open');
  assert.match(fix, /narrow the query/);
  assert.match(fix, /repo: or org:/);
  assert.match(fix, /Retrying will/);
  assert.match(repair('timed-out-intermittent', 'is:issue'), /never cache it/);
});

test('the check refuses a plan that would not fit the search bucket', () => {
  assert.equal(readCost(['q'], 3), 3);
  assert.equal(readCost(['a', 'b'], 4), 8);
  assert.equal(readCost([], 3), 0);
  assert.ok(withinSearchBucket(3));
  assert.ok(withinSearchBucket(SEARCH_BUCKET));
  assert.ok(!withinSearchBucket(SEARCH_BUCKET + 1));
  assert.ok(!withinSearchBucket(0));
});
