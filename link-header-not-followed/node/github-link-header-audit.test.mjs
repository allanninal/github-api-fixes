import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pageNumber, parseLink, verdict } from './github-link-header-audit.mjs';

const FULL =
  '<https://api.github.com/repositories/1/pulls?per_page=1&page=2>; rel="next", ' +
  '<https://api.github.com/repositories/1/pulls?per_page=1&page=340>; rel="last"';

test('link header parses both relations', () => {
  const links = parseLink(FULL);
  assert.deepEqual([...links.keys()].sort(), ['last', 'next']);
  assert.equal(pageNumber(links.get('last')), 340);
});

test('a comma inside a url does not become a second link', () => {
  const header =
    '<https://api.github.com/repos/o/n/issues?labels=bug,ci&page=2>; rel="next", ' +
    '<https://api.github.com/repos/o/n/issues?labels=bug,ci&page=9>; rel="last"';
  const links = parseLink(header);
  assert.deepEqual([...links.keys()].sort(), ['last', 'next']);
  assert.match(links.get('next'), /labels=bug,ci&page=2$/);
});

test('no link header is a single page', () => {
  const [state, detail] = verdict(parseLink(null), 7, 1);
  assert.equal(state, 'single-page');
  assert.match(detail, /7 item\(s\)/);
});

test('rel=last at per_page=1 is the exact count', () => {
  const [state, detail] = verdict(parseLink(FULL), 1, 1);
  assert.equal(state, 'more-pages');
  assert.match(detail, /340 item\(s\)/);
});

test('next without last is its own state', () => {
  const header = '<https://api.github.com/repos/o/n/branches?page=2>; rel="next"';
  const [state, detail] = verdict(parseLink(header), 1, 1);
  assert.equal(state, 'more-pages-unsized');
  assert.match(detail, /rel="last"/);
});

test('page number is null when there is no page parameter', () => {
  assert.equal(pageNumber('https://api.github.com/repos/o/n/pulls?per_page=100'), null);
  assert.equal(pageNumber(null), null);
});
