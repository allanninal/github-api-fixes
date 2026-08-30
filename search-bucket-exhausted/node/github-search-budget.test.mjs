import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  bucketPressure, planLoop, packRepoQueries, verdict,
} from './github-search-budget.mjs';

const NOW = 1800000000;

const RESOURCES = {
  core: { limit: 5000, used: 120, remaining: 4880, reset: NOW + 2400 },
  search: { limit: 30, used: 4, remaining: 26, reset: NOW + 41 },
  code_search: { limit: 10, used: 0, remaining: 10, reset: NOW + 55 },
  graphql: { limit: 5000, used: 0, remaining: 5000, reset: NOW + 2400 },
};

test('the two buckets only compare once the windows match', () => {
  const p = bucketPressure(RESOURCES, NOW);
  assert.equal(p.core.per_minute, Math.round((5000 / 60) * 10) / 10);
  assert.equal(p.search.per_minute, 30);
  assert.ok(p.search.per_minute < p.core.per_minute);
});

test('code search is tighter still', () => {
  assert.equal(bucketPressure(RESOURCES, NOW).code_search.per_minute, 10);
});

test('a bucket with an unknown window is reported, not guessed', () => {
  const p = bucketPressure({ something_new: { limit: 99, used: 1, reset: NOW } }, NOW);
  assert.equal(p.something_new.per_minute, null);
  assert.equal(p.something_new.limit, 99);
});

test('a malformed bucket is skipped', () => {
  assert.deepEqual(bucketPressure({ core: { limit: 'lots' } }, NOW), {});
  assert.deepEqual(bucketPressure(null, NOW), {});
});

test('refills_in never goes negative', () => {
  const p = bucketPressure({ search: { limit: 30, used: 30, reset: NOW - 90 } }, NOW);
  assert.equal(p.search.refills_in, 0);
});

test('a loop longer than the window refuses the surplus', () => {
  const plan = planLoop(400, 30);
  assert.equal(plan.minutes, 13.3);
  assert.equal(plan.refused_in_first_minute, 370);
});

test('a loop inside the window refuses nothing', () => {
  assert.equal(planLoop(12, 30).refused_in_first_minute, 0);
});

test('a missing rate is not treated as infinite', () => {
  const plan = planLoop(400, null);
  assert.equal(plan.minutes, null);
  assert.equal(plan.refused_in_first_minute, null);
});

test('a short list becomes one query', () => {
  const packed = packRepoQueries(['octo/one', 'octo/two'], 'is:issue is:open');
  assert.equal(packed.queries.length, 1);
  assert.ok(packed.queries[0].startsWith('is:issue is:open repo:octo/one'));
  assert.ok(packed.queries[0].length <= 256);
});

test('a long list splits and every query fits', () => {
  const repos = Array.from({ length: 40 }, (_, i) => `acme/service-${String(i).padStart(2, '0')}`);
  const packed = packRepoQueries(repos, 'is:issue is:open label:bug');
  assert.ok(packed.queries.length > 1);
  assert.ok(packed.queries.every((q) => q.length <= 256));
  const joined = packed.queries.join(' ');
  for (const r of repos) {
    assert.equal(joined.split(`repo:${r}`).length - 1, 1);
  }
  assert.ok(packed.queries.length < 8);
});

test('a repository that cannot fit beside the base query is named', () => {
  const huge = 'acme/' + 'x'.repeat(250);
  const packed = packRepoQueries([huge, 'acme/ok'], 'is:issue');
  assert.deepEqual(packed.too_long, [huge]);
  assert.deepEqual(packed.queries, ['is:issue repo:acme/ok']);
});

test('empty input packs into nothing', () => {
  assert.deepEqual(packRepoQueries([], 'is:issue').queries, []);
  assert.deepEqual(packRepoQueries(null).queries, []);
  assert.deepEqual(packRepoQueries(['', '  ']).queries, []);
});

test('boolean operators in the base query are counted', () => {
  const packed = packRepoQueries(['a/b'], 'cat OR dog OR bird OR fish OR rat OR ox');
  assert.equal(packed.operators, 5);
  assert.equal(packed.over_operator_limit, false);
  assert.equal(packRepoQueries(['a/b'], 'a OR b OR c OR d OR e OR f OR g').over_operator_limit, true);
});

test('an empty search bucket points at the healthy core one', () => {
  const p = bucketPressure({
    ...RESOURCES,
    search: { limit: 30, used: 30, remaining: 0, reset: NOW + 12 },
  }, NOW);
  const [state, detail] = verdict(p.search, p.core);
  assert.equal(state, 'exhausted');
  assert.match(detail, /different buckets/);
  assert.match(detail, /12 second\(s\)/);
});

test('an oversized loop reports the packed alternative', () => {
  const p = bucketPressure(RESOURCES, NOW);
  const repos = Array.from({ length: 400 }, (_, i) => `acme/service-${String(i).padStart(2, '0')}`);
  const [state, detail] = verdict(p.search, p.core,
    planLoop(400, p.search.per_minute), packRepoQueries(repos, 'is:issue is:open'));
  assert.equal(state, 'over-budget');
  assert.match(detail, /refused inside the first minute/);
  assert.match(detail, /queries/);
});

test('the core comparison is stated in the same units', () => {
  const p = bucketPressure(RESOURCES, NOW);
  assert.match(verdict(p.search, p.core)[1], /83 a minute/);
});

test('a healthy bucket with no plan is clear', () => {
  const p = bucketPressure(RESOURCES, NOW);
  assert.equal(verdict(p.search, p.core)[0], 'clear');
});

test('no search bucket is not reported as healthy', () => {
  assert.equal(verdict(null, null)[0], 'no-search-bucket');
});
