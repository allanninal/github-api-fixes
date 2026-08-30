import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  CAPS, DEFAULT_PER_PAGE, MAX_PER_PAGE, beyondCap, boundsFromLast, capFor,
  counterOutsideBounds, lastPageFrom, onePageShortfall, pageOf, pagesNeeded,
  parseLink, readCost, reachable, repair, verdict,
} from './github-pr-truncation.mjs';

test('the two ceilings are different numbers', () => {
  assert.equal(capFor('files'), 3000);
  assert.equal(capFor('commits'), 250);
  assert.equal(capFor('comments'), null);
  assert.ok(CAPS.files > CAPS.commits);
});

test('pagesNeeded rounds up and refuses nonsense', () => {
  assert.equal(pagesNeeded(3000, 100), 30);
  assert.equal(pagesNeeded(901, 100), 10);
  assert.equal(pagesNeeded(1, 100), 1);
  assert.equal(pagesNeeded(0, 100), 0);
  assert.equal(pagesNeeded(250, 30), 9);
  assert.equal(pagesNeeded(10, 0), null);
  assert.equal(pagesNeeded(null, 100), null);
});

test('what is reachable stops at the ceiling', () => {
  assert.equal(reachable('files', 4200), 3000);
  assert.equal(reachable('files', 12), 12);
  assert.equal(reachable('commits', 812), 250);
  assert.equal(reachable('files', null), null);
});

test('what is beyond the ceiling is counted exactly', () => {
  assert.equal(beyondCap('files', 4200), 1200);
  assert.equal(beyondCap('files', 3000), 0);
  assert.equal(beyondCap('commits', 251), 1);
  assert.equal(beyondCap('commits', null), 0);
});

test('a last page implies a band rather than a number', () => {
  assert.deepEqual(boundsFromLast(3, 100), [201, 300]);
  assert.deepEqual(boundsFromLast(1, 100), [1, 100]);
  assert.equal(boundsFromLast(0, 100), null);
  assert.equal(boundsFromLast(null, 100), null);
  assert.equal(boundsFromLast(3, 0), null);
});

test('the counter is only wrong when it leaves the band', () => {
  assert.ok(counterOutsideBounds(150, [1, 100]));
  assert.ok(counterOutsideBounds(0, [1, 100]));
  assert.ok(!counterOutsideBounds(100, [1, 100]));
  assert.ok(!counterOutsideBounds(250, [201, 300]));
  assert.ok(!counterOutsideBounds(150, null));
});

test('one default page is where most of it is lost', () => {
  assert.equal(onePageShortfall(900), 900 - DEFAULT_PER_PAGE);
  assert.equal(onePageShortfall(31), 1);
  assert.equal(onePageShortfall(30), 0);
  assert.equal(onePageShortfall(7), 0);
  assert.equal(onePageShortfall(900, 100), 800);
});

test('a count above the ceiling is unreachable at any page size', () => {
  const [state, detail] = verdict('files', 4200, 30, MAX_PER_PAGE);
  assert.equal(state, 'beyond-cap');
  assert.match(detail, /1200/);
  assert.match(detail, /any page size/);
  assert.equal(verdict('commits', 812, 3, MAX_PER_PAGE)[0], 'beyond-cap');
});

test('a page count that cannot hold the counter is its own finding', () => {
  const [state, detail] = verdict('files', 150, 1, MAX_PER_PAGE);
  assert.equal(state, 'counter-disagrees');
  assert.match(detail, /between 1 and 100/);
});

test('a reconcilable multi-page list names what one page misses', () => {
  const [state, detail] = verdict('files', 900, 9, MAX_PER_PAGE);
  assert.equal(state, 'multi-page');
  assert.match(detail, /misses 870/);
});

test('a small pull request is not a finding', () => {
  assert.equal(verdict('files', 7, 1, MAX_PER_PAGE)[0], 'single-page');
  assert.equal(verdict('commits', 30, 1, MAX_PER_PAGE)[0], 'single-page');
});

test('a missing counter is reported rather than assumed', () => {
  assert.equal(verdict('files', null, 1, MAX_PER_PAGE)[0], 'unknown');
  assert.equal(verdict('files', 'several', 1, MAX_PER_PAGE)[0], 'unknown');
  assert.equal(verdict('comments', 12, 1, MAX_PER_PAGE)[0], 'unknown');
});

test('an unknown page count does not manufacture a disagreement', () => {
  assert.equal(verdict('files', 900, null, MAX_PER_PAGE)[0], 'multi-page');
});

test('the two repairs are not interchangeable', () => {
  assert.match(repair('beyond-cap', 'files'), /vnd\.github\.diff/);
  assert.ok(!repair('beyond-cap', 'commits').includes('vnd.github.diff'));
  assert.match(repair('beyond-cap', 'commits'), /\/commits/);
  assert.match(repair('multi-page', 'files'), /per_page=100/);
  assert.match(repair('counter-disagrees', 'files'), /changed_files/);
  assert.ok(repair('single-page', 'files').startsWith('nothing on this'));
});

test('the page count is read from the header, not guessed', () => {
  const header = '<https://api.github.com/repos/o/n/pulls/1/files?page=2>; rel="next", '
    + '<https://api.github.com/repos/o/n/pulls/1/files?page=9>; rel="last"';
  const links = parseLink(header);
  assert.equal(pageOf(links.last), 9);
  assert.equal(lastPageFrom(links), 9);
  assert.equal(lastPageFrom({ next: 'https://api.github.com/x?page=2' }), null);
  assert.equal(lastPageFrom({}), 1);
  assert.equal(pageOf(null), null);
});

test('the run says what it will spend', () => {
  assert.equal(readCost([1, 2]), 6);
  assert.equal(readCost([4821]), 3);
  assert.equal(readCost([]), 0);
  assert.equal(readCost(null), 0);
});
