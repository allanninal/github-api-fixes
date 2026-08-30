import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  cursorHint, identities, identity, linkParams, linkStyle, loopTerminates,
  overlaps, parseLink, readCost, repair, sameRows, verdict,
} from './github-page-param-ignored.mjs';

const BASE = 'https://api.github.com/repos/o/n';
const OFFSET_LINK = { next: `${BASE}/issues?per_page=1&page=2` };
const CURSOR_LINK = { next: `${BASE}/activity?per_page=1&after=Y3Vyc29yOjE=` };
const BEFORE_LINK = { next: `${BASE}/activity?per_page=1&before=Y3Vyc29yOjk=` };
const NO_LINK = {};

test('the link style is read from the parameter names', () => {
  assert.equal(linkStyle(CURSOR_LINK), 'cursor');
  assert.equal(linkStyle(BEFORE_LINK), 'cursor');
  assert.equal(linkStyle(OFFSET_LINK), 'offset');
  assert.equal(linkStyle(NO_LINK), 'none');
  assert.equal(linkStyle(null), 'none');
});

test('the cursor parameter is named so the repair can be concrete', () => {
  assert.equal(cursorHint(CURSOR_LINK), 'after');
  assert.equal(cursorHint(BEFORE_LINK), 'before');
  assert.equal(cursorHint(OFFSET_LINK), null);
  assert.deepEqual(linkParams(OFFSET_LINK), ['page', 'per_page']);
});

test('identifiers fall back through the fields a list might use', () => {
  assert.equal(identity({ id: 41, node_id: 'MDQ6' }), '41');
  assert.equal(identity({ node_id: 'MDQ6' }), 'MDQ6');
  assert.equal(identity({ sha: '9f2c1ab' }), '9f2c1ab');
  assert.equal(identity({ url: `${BASE}/pulls/3` }), `${BASE}/pulls/3`);
  assert.equal(identity({ title: 'no identifier here' }), null);
  assert.equal(identity(null), null);
});

test('a page of unidentifiable items does not become a finding', () => {
  assert.deepEqual(identities([{ title: 'a' }, { title: 'b' }]), []);
  assert.deepEqual(identities([{ id: 1 }, { title: 'b' }]), ['1']);
  assert.deepEqual(identities('not a list'), []);
});

test('identical rows with a cursor link is the definite finding', () => {
  const [state, detail] = verdict('cursor', ['9'], ['9']);
  assert.equal(state, 'ignores-page');
  assert.match(detail, /does not read page at all/);
  assert.match(detail, /no terminating condition/);
  assert.ok(!loopTerminates(state));
});

test('identical rows with a page link is only a suspicion', () => {
  const [state, detail] = verdict('offset', ['9'], ['9']);
  assert.equal(state, 'suspect-ignores-page');
  assert.match(detail, /may be a feed that moved/);
  assert.match(detail, /Re-run it/);
});

test('a partial overlap is its own answer', () => {
  const [state, detail] = verdict('offset', ['9', '8'], ['8', '7']);
  assert.equal(state, 'overlapping-pages');
  assert.match(detail, /unstable sort/);
  assert.ok(loopTerminates(state));
});

test('a cursor endpoint that pages properly is not a finding', () => {
  const [state, detail] = verdict('cursor', ['9'], ['8']);
  assert.equal(state, 'cursor-pagination');
  assert.match(detail, /not by number/);
});

test('offset pagination that works is reported as working', () => {
  assert.equal(verdict('offset', ['9'], ['8'])[0], 'offset-honoured');
  assert.equal(verdict('offset', ['9'], [])[0], 'offset-honoured');
});

test('an empty first page proves nothing', () => {
  const [state, detail] = verdict('none', [], []);
  assert.equal(state, 'inconclusive-empty');
  assert.match(detail, /no comparison to make/);
});

test('the row comparisons are order sensitive and set based in turn', () => {
  assert.ok(sameRows(['1', '2'], ['1', '2']));
  assert.ok(!sameRows(['1', '2'], ['2', '1']));
  assert.ok(!sameRows([], []));
  assert.ok(overlaps(['1', '2'], ['2', '3']));
  assert.ok(!overlaps(['1'], ['2']));
});

test('the repair names the cursor the endpoint actually uses', () => {
  assert.match(repair('ignores-page', CURSOR_LINK), /after=/);
  assert.match(repair('ignores-page', BEFORE_LINK), /before=/);
  assert.match(repair('ignores-page', NO_LINK), /no next page/);
  assert.ok(!repair('cursor-pagination').includes('Re-run'));
});

test('the repair for a suspicion changes no code', () => {
  const fix = repair('suspect-ignores-page');
  assert.match(fix, /re-run the check/);
  assert.match(fix, /before changing any code/);
});

test('the header is parsed around commas inside URLs', () => {
  const header = `<${BASE}/issues?labels=bug,ci&page=2>; rel="next"`;
  assert.equal(linkStyle(parseLink(header)), 'offset');
});

test('the run says what it will spend', () => {
  assert.equal(readCost(['/a', '/b']), 4);
  assert.equal(readCost([]), 0);
  assert.equal(readCost(null), 0);
});
