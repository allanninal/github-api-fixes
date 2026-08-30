import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  capabilities, itemCount, naivePageCount, pageCount, pageParam,
  paginationStyle, parseLink, readCost, rels, repair, unavailable, verdict,
} from './github-rel-last-absent.mjs';

const BASE = 'https://api.github.com/repositories/1/issues';
const INDEXABLE = { next: `${BASE}?page=2`, last: `${BASE}?page=912` };
const WALK_ONLY = { next: `${BASE}?page=2` };
const DEEP = {
  first: `${BASE}?page=1`, prev: `${BASE}?page=4`, next: `${BASE}?page=6`, last: `${BASE}?page=40`,
};
const SINGLE = {};

test('the classification is three states, not two', () => {
  assert.equal(paginationStyle(INDEXABLE), 'indexable');
  assert.equal(paginationStyle(WALK_ONLY), 'walk-only');
  assert.equal(paginationStyle(SINGLE), 'single-page');
  assert.equal(paginationStyle(null), 'single-page');
});

test('walk-only is never mistaken for a single page', () => {
  assert.notEqual(paginationStyle(WALK_ONLY), paginationStyle(SINGLE));
  assert.notDeepEqual(capabilities('walk-only'), capabilities('single-page'));
});

test('a careful page count refuses to answer without the field', () => {
  assert.equal(pageCount(INDEXABLE), 912);
  assert.equal(pageCount(DEEP), 40);
  assert.equal(pageCount(SINGLE), 1);
  assert.equal(pageCount(WALK_ONLY), null);
});

test('the careless page count turns the gap into one', () => {
  assert.equal(naivePageCount(WALK_ONLY), 1);
  assert.equal(naivePageCount(INDEXABLE), 912);
  assert.notEqual(naivePageCount(WALK_ONLY), pageCount(WALK_ONLY));
  assert.equal(naivePageCount(INDEXABLE), pageCount(INDEXABLE));
});

test('the item count is only offered at a page size of one', () => {
  assert.equal(itemCount(INDEXABLE, 1), 912);
  assert.equal(itemCount(INDEXABLE, 100), null);
  assert.equal(itemCount(WALK_ONLY, 1), null);
});

test('the capability table says what a pager may rely on', () => {
  const walk = capabilities('walk-only');
  assert.equal(walk.walk, true);
  assert.equal(walk.page_count, false);
  assert.equal(walk.progress_bar, false);
  assert.equal(walk.parallel_fanout, false);
  assert.equal(walk.jump_to_last, false);
  assert.equal(capabilities('indexable').parallel_fanout, true);
});

test('the capability table is a copy so a caller cannot edit it', () => {
  capabilities('indexable').page_count = false;
  assert.equal(capabilities('indexable').page_count, true);
});

test('the broken patterns are named in a fixed order', () => {
  assert.deepEqual(unavailable('walk-only'),
    ['page count', 'progress bar', 'parallel fan-out', 'jump to last']);
  assert.deepEqual(unavailable('indexable'), []);
  assert.deepEqual(unavailable('single-page'), ['parallel fan-out', 'jump to last']);
});

test('the walk-only verdict prints the number that moves somebody', () => {
  const [state, detail] = verdict(WALK_ONLY);
  assert.equal(state, 'walk-only');
  assert.match(detail, /only knowable by walking it/);
  assert.match(detail, /reports 1 page/);
});

test('an indexable endpoint is reported as a snapshot', () => {
  const [state, detail] = verdict(INDEXABLE, 1);
  assert.equal(state, 'indexable');
  assert.match(detail, /912/);
  assert.match(detail, /moves between calls/);
});

test('a single-page list is not a pagination finding', () => {
  const [state, detail] = verdict(SINGLE);
  assert.equal(state, 'single-page');
  assert.match(detail, /nothing about paging applies/);
  assert.equal(repair(state), 'nothing.');
});

test('the page parameter is read out of the URL defensively', () => {
  assert.equal(pageParam(`${BASE}?page=7&per_page=1`), 7);
  assert.equal(pageParam(`${BASE}?per_page=1`), null);
  assert.equal(pageParam(''), null);
  assert.equal(pageParam(null), null);
});

test('the header is parsed around commas inside URLs', () => {
  const header = `<${BASE}?labels=bug,ci&page=2>; rel="next", `
    + `<${BASE}?labels=bug,ci&page=9>; rel="last"`;
  assert.deepEqual(rels(parseLink(header)), ['last', 'next']);
  assert.deepEqual(parseLink(''), {});
});

test('the repair for walk-only never asks for a page count', () => {
  const fix = repair('walk-only');
  assert.match(fix, /rel="next"/);
  assert.match(fix, /never require/);
  assert.match(repair('indexable'), /cache it as the size of the job/);
});

test('the run says what it will spend', () => {
  assert.equal(readCost(['/a', '/b', '/c', '/d', '/e']), 5);
  assert.equal(readCost([]), 0);
  assert.equal(readCost(null), 0);
});
