import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  buckets, collapsedCost, scanCost, secondsUntil, verdict,
} from './github-code-search-budget.mjs';

const PAYLOAD = {
  resources: {
    core: { limit: 5000, remaining: 4987, reset: 1700000000 },
    search: { limit: 30, remaining: 30, reset: 1700000060 },
    code_search: { limit: 10, remaining: 0, reset: 1700000060 },
  },
};

test('every documented bucket is reported separately', () => {
  const table = buckets(PAYLOAD);
  assert.equal(table.core.remaining, 4987);
  assert.equal(table.code_search.remaining, 0);
  assert.equal(table.code_search.limit, 10);
});

test('a missing row is flagged rather than read as zero', () => {
  const table = buckets({ resources: { core: { limit: 5000, remaining: 10 } } });
  assert.equal(table.code_search.present, false);
  assert.equal(table.code_search.remaining, null);
  assert.equal(table.code_search.limit, 10);
});

test('an empty payload still returns the full table', () => {
  const table = buckets(null);
  assert.deepEqual(Object.keys(table).sort(), ['code_search', 'core', 'search']);
  assert.ok(Object.values(table).every((row) => row.present === false));
});

test('unreadable numbers do not become zero', () => {
  const table = buckets({ resources: { code_search: { limit: 'ten', remaining: null } } });
  assert.equal(table.code_search.limit, 10);
  assert.equal(table.code_search.remaining, null);
});

test('a per-repo scan costs repositories, not pages', () => {
  const cost = scanCost(600, 1, 10);
  assert.equal(cost.requests, 600);
  assert.equal(cost.minutes, 60);
});

test('minutes round up because a partial minute still waits', () => {
  assert.equal(scanCost(11, 1, 10).minutes, 2);
  assert.deepEqual(scanCost(0, 3, 10), { requests: 0, minutes: 0 });
});

test('the collapsed scan costs pages', () => {
  const cost = collapsedCost(1, 800, 10);
  assert.equal(cost.pages_per_query, 8);
  assert.equal(cost.requests, 8);
  assert.equal(cost.minutes, 1);
  assert.equal(cost.truncated, false);
});

test('paging stops at the thousand-result ceiling', () => {
  const cost = collapsedCost(1, 50000, 10);
  assert.equal(cost.pages_per_query, 10);
  assert.equal(cost.truncated, true);
});

test('a query with no matches still costs one request', () => {
  assert.equal(collapsedCost(3, 0, 10).requests, 3);
});

test('the page size cannot be raised past a hundred', () => {
  assert.equal(collapsedCost(1, 500, 10, 500).pages_per_query, 5);
});

test('secondsUntil floors at zero and reports junk as unknown', () => {
  assert.equal(secondsUntil(1700000060, 1700000000), 60);
  assert.equal(secondsUntil(1700000000, 1700000060), 0);
  assert.equal(secondsUntil(null, 1700000000), null);
});

test('an empty code_search bucket is not the hourly quota', () => {
  const [state, detail] = verdict(buckets(PAYLOAD).code_search,
    scanCost(600, 1, 10), collapsedCost(1, 800, 10));
  assert.equal(state, 'exhausted');
  assert.match(detail, /not the core quota/);
});

test('the loop is named as the cost when it dwarfs the query', () => {
  const bucket = { limit: 10, remaining: 10, reset: 0, present: true };
  const [state, detail] = verdict(bucket, scanCost(600, 1, 10), collapsedCost(1, 800, 10));
  assert.equal(state, 'per-repo-scan');
  assert.match(detail, /600 request\(s\)/);
  assert.match(detail, /8 request\(s\)/);
});

test('a scan inside one minute is clear', () => {
  const bucket = { limit: 10, remaining: 10, reset: 0, present: true };
  assert.equal(verdict(bucket, scanCost(5, 1, 10), collapsedCost(1, 200, 10))[0], 'clear');
});

test('a missing row is said out loud in the verdict', () => {
  const bucket = { limit: 10, remaining: null, reset: null, present: false };
  const [, detail] = verdict(bucket, scanCost(5, 1, 10), collapsedCost(1, 200, 10));
  assert.match(detail, /documented default/);
});

test('nothing to cost is its own state', () => {
  const bucket = { limit: 10, remaining: 10, reset: 0, present: true };
  assert.equal(verdict(bucket, scanCost(0, 0, 10), collapsedCost(1, 200, 10))[0], 'no-scan');
});
