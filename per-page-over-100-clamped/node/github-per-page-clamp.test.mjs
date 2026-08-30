import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  MAX_PER_PAGE, clampedTo, isOverMaximum, parseLink, predicatesDisagree,
  readCost, repair, stopsOnMissingNext, stopsOnShortPage, verdict,
} from './github-per-page-clamp.mjs';

const MORE = { next: 'https://api.github.com/repositories/1/issues?page=2' };
const END = { prev: 'https://api.github.com/repositories/1/issues?page=3' };

test('the clamp is a minimum, not a rejection', () => {
  assert.equal(clampedTo(500), MAX_PER_PAGE);
  assert.equal(clampedTo(101), MAX_PER_PAGE);
  assert.equal(clampedTo(100), 100);
  assert.equal(clampedTo(30), 30);
  assert.equal(clampedTo('50'), 50);
});

test('a page size that is not one is reported rather than guessed', () => {
  assert.equal(clampedTo(0), null);
  assert.equal(clampedTo(-5), null);
  assert.equal(clampedTo(null), null);
  assert.equal(clampedTo('many'), null);
});

test('only values above the maximum are lowered', () => {
  assert.ok(isOverMaximum(500));
  assert.ok(isOverMaximum(101));
  assert.ok(!isOverMaximum(100));
  assert.ok(!isOverMaximum(null));
});

test('the short-page check is wrong on a clamped response', () => {
  assert.ok(stopsOnShortPage(500, 100));
  assert.ok(!stopsOnShortPage(100, 100));
  assert.ok(stopsOnShortPage(100, 42));
});

test('the header check does not care about page sizes', () => {
  assert.ok(!stopsOnMissingNext(MORE));
  assert.ok(stopsOnMissingNext(END));
  assert.ok(stopsOnMissingNext({}));
  assert.ok(stopsOnMissingNext(null));
});

test('the finding is exactly the disagreement', () => {
  assert.ok(predicatesDisagree(500, 100, MORE));
  assert.ok(!predicatesDisagree(500, 100, END));
  assert.ok(!predicatesDisagree(100, 100, MORE));
});

test('a clamped page with more behind it is the finding', () => {
  const [state, detail] = verdict(500, 100, MORE);
  assert.equal(state, 'clamped-and-truncated');
  assert.match(detail, /reduced to 100/);
  assert.match(detail, /stops on a short page/);
});

test('a collection that ends on the boundary is still a trap', () => {
  const [state, detail] = verdict(500, 100, END);
  assert.equal(state, 'clamped-at-boundary');
  assert.match(detail, /item 101/);
});

test('a small collection cannot prove the clamp', () => {
  const [state, detail] = verdict(500, 12, {});
  assert.equal(state, 'clamped-untested');
  assert.match(detail, /cannot be shown on this path/);
});

test('an endpoint with a smaller maximum is named separately', () => {
  const [state, detail] = verdict(100, 50, MORE);
  assert.equal(state, 'smaller-maximum');
  assert.match(detail, /smaller page than you requested/);
});

test('a full page within the cap is not a finding', () => {
  assert.equal(verdict(100, 100, MORE)[0], 'within-cap-more-pages');
  assert.equal(verdict(100, 100, END)[0], 'within-cap-complete');
  assert.equal(verdict(30, 11, {})[0], 'within-cap-complete');
});

test('an unreadable response is not reported as a clamp', () => {
  assert.equal(verdict(500, null, MORE)[0], 'unknown');
  assert.equal(verdict(null, 100, MORE)[0], 'unknown');
});

test('the Link header survives a comma inside a URL', () => {
  const header = '<https://api.github.com/repos/o/n/issues?labels=bug,ci&page=2>; rel="next", '
    + '<https://api.github.com/repos/o/n/issues?labels=bug,ci&page=9>; rel="last"';
  const links = parseLink(header);
  assert.deepEqual(Object.keys(links).sort(), ['last', 'next']);
  assert.ok(links.next.endsWith('page=2'));
  assert.deepEqual(parseLink(null), {});
});

test('the repair never suggests asking for more than the maximum', () => {
  for (const state of ['clamped-and-truncated', 'clamped-at-boundary', 'clamped-untested']) {
    assert.match(repair(state), /per_page=100/);
    assert.ok(!repair(state).includes('500'));
  }
  assert.match(repair('smaller-maximum'), /smaller page than 100/);
  assert.equal(repair('within-cap-complete'), 'nothing.');
});

test('the run says what it will spend', () => {
  assert.equal(readCost(['/a', '/b', '/c']), 3);
  assert.equal(readCost(['/a', '/b', '/c'], true), 6);
  assert.equal(readCost([]), 0);
  assert.equal(readCost(null), 0);
});
