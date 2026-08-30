import { test } from 'node:test';
import assert from 'node:assert/strict';
import { lastReachablePage, reach } from './github-search-cap-audit.mjs';

test('a small query is fully reachable', () => {
  const [state, detail] = reach(240, 100);
  assert.equal(state, 'reachable');
  assert.match(detail, /3 request\(s\)/);
});

test('a query over the cap names what is unreachable', () => {
  const [state, detail] = reach(24831, 100);
  assert.equal(state, 'capped');
  assert.match(detail, /23831 match\(es\)/);
  assert.match(detail, /at least 25 narrower queries/);
});

test('just under the cap is a warning, not a pass', () => {
  const [state, detail] = reach(950, 100);
  assert.equal(state, 'near-cap');
  assert.match(detail, /950/);
});

test('no matches is not confused with a capped query', () => {
  assert.equal(reach(0, 100)[0], 'no-matches');
  assert.equal(reach(null, 100)[0], 'no-matches');
});

test('the last working page depends on the page size', () => {
  assert.equal(lastReachablePage(100), 10);
  assert.equal(lastReachablePage(30), 33);
  assert.equal(lastReachablePage(1), 1000);
});

test('page size above the maximum is clamped before the arithmetic', () => {
  assert.equal(lastReachablePage(500), 10);
});
