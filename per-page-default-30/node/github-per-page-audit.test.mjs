import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pagesFor, verdict } from './github-per-page-audit.mjs';

test('page count is a ceiling, not a division', () => {
  assert.equal(pagesFor(3000, 30), 100);
  assert.equal(pagesFor(3000, 100), 30);
  assert.equal(pagesFor(3001, 100), 31);
  assert.equal(pagesFor(1, 100), 1);
});

test('per_page above the maximum is clamped to 100', () => {
  assert.equal(pagesFor(3000, 500), 30);
  const [state, detail] = verdict(3000, 500);
  assert.equal(state, 'at-maximum');
  assert.match(detail, /clamped/);
});

test('the default page size is the finding', () => {
  const [state, detail] = verdict(3412, 30);
  assert.equal(state, 'wasteful');
  assert.match(detail, /114 request\(s\) at per_page=30/);
  assert.match(detail, /35 at per_page=100/);
  assert.match(detail, /79 request\(s\)/);
});

test('a full page size has nothing to recover', () => {
  assert.equal(verdict(3412, 100)[0], 'at-maximum');
});

test('a short list is one request either way', () => {
  assert.equal(verdict(12, 30)[0], 'single-page');
});

test('an empty collection is not reported as wasteful', () => {
  assert.equal(verdict(0, 30)[0], 'empty');
  assert.equal(pagesFor(0, 30), 0);
});
