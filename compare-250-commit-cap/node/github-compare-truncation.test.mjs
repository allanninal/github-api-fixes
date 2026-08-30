import { test } from 'node:test';
import assert from 'node:assert/strict';
import { verdict } from './github-compare-truncation.mjs';

const compare = (total, received, files = 0) => ({
  total_commits: total,
  commits: Array.from({ length: received }, (_, i) => ({ sha: String(i) })),
  files: Array.from({ length: files }, (_, i) => ({ filename: `f${i}` })),
});

test('a small comparison is complete', () => {
  const [state, detail] = verdict(compare(18, 18, 42));
  assert.equal(state, 'complete');
  assert.match(detail, /18 commit\(s\)/);
  assert.match(detail, /42 changed file\(s\)/);
});

test('exactly 250 with more to come is the cap', () => {
  const [state, detail] = verdict(compare(812, 250));
  assert.equal(state, 'capped');
  assert.match(detail, /562 commit\(s\) are missing/);
  assert.match(detail, /not the 250th commit/);
});

test('a partial page is not the same finding as the cap', () => {
  const [state, detail] = verdict(compare(812, 100));
  assert.equal(state, 'truncated');
  assert.match(detail, /712 commit\(s\) are missing/);
});

test('no commits between the refs is not a failure', () => {
  assert.equal(verdict(compare(0, 0))[0], 'empty');
});

test('a missing total_commits is never reported as complete', () => {
  assert.equal(verdict({ commits: [{ sha: 'abc' }] })[0], 'unknown');
});
