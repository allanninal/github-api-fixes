import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  MAX_PAGE_SIZE, SEARCH_RESULT_CEILING, classifyWalk, matchCount, operations,
  pagesToCeiling, pointCost, reachable, refusal, repair, slicesNeeded,
  truncationSignal, typedConnectionFor, unreachable,
} from './github-graphql-search-ceiling.mjs';

test('the ceiling is a property of the index not the page size', () => {
  assert.equal(SEARCH_RESULT_CEILING, 1000);
  assert.equal(MAX_PAGE_SIZE, 100);
  assert.equal(pagesToCeiling(100), 10);
  assert.equal(pagesToCeiling(30), 34);
  assert.equal(pagesToCeiling(1), 1000);
});

test('a match count splits into a reachable and an unreachable half', () => {
  assert.equal(reachable(18231), 1000);
  assert.equal(unreachable(18231), 17231);
  assert.equal(reachable(400), 400);
  assert.equal(unreachable(400), 0);
  assert.equal(reachable(null), 0);
  assert.equal(unreachable('not a number'), 0);
});

test('the ceiling stop and a complete walk are the same shape', () => {
  const [hit, detail] = classifyWalk(18231, 1000, false, 10, 11);
  const [done] = classifyWalk(40, 40, false, 1, 11);
  assert.equal(hit, 'ceiling-hit-silently');
  assert.equal(done, 'complete');
  assert.match(detail, /No error was raised/);
  assert.match(detail, /18231/);
});

test('a walk cut short by the operator proves nothing', () => {
  const [state, detail] = classifyWalk(18231, 500, true, 5, 5);
  assert.equal(state, 'stopped-early-by-request');
  assert.match(detail, /nothing about the ceiling is proved/);
  assert.match(repair(state, 18231, 'ISSUE'), /at least 10/);
});

test('a walk still going is not a finding', () => {
  assert.equal(classifyWalk(18231, 300, true, 3, 11)[0], 'still-paging');
});

test('ending below the ceiling is a different note', () => {
  const [state, detail] = classifyWalk(900, 640, false, 7, 11);
  assert.equal(state, 'truncated-early');
  assert.match(detail, /not this note/);
  assert.match(repair(state, 900, 'ISSUE'), /search-incomplete-results/);
});

test('the repair names a ceiling free connection and a slice count', () => {
  const fix = repair('ceiling-hit-silently', 18231, 'ISSUE');
  assert.match(fix, /repository\.issues/);
  assert.match(fix, /19 slice\(s\)/);
  assert.match(repair('ceiling-hit-silently', 4000, 'REPOSITORY'),
    /organization\.repositories/);
});

test('a partition needs one slice per thousand matches', () => {
  assert.equal(slicesNeeded(18231), 19);
  assert.equal(slicesNeeded(1000), 1);
  assert.equal(slicesNeeded(1001), 2);
  assert.equal(slicesNeeded(0), 0);
});

test('every search type has a connection that has no ceiling', () => {
  assert.match(typedConnectionFor('ISSUE'), /repository\.issues/);
  assert.match(typedConnectionFor('repository'), /organization\.repositories/);
  assert.match(typedConnectionFor('USER'), /membersWithRole/);
  assert.match(typedConnectionFor('DISCUSSION'), /discussions/);
  assert.ok(typedConnectionFor('SOMETHING_ELSE').startsWith('the typed connection'));
});

test('the two apis announce the same ceiling differently', () => {
  assert.match(truncationSignal('rest'), /422/);
  assert.match(truncationSignal('rest'), /1000 search results/);
  assert.match(truncationSignal('graphql'), /no error at all/);
  assert.match(truncationSignal('graphql'), /hasNextPage/);
});

test('the count field follows the search type', () => {
  const search = { issueCount: 18231, repositoryCount: 12, userCount: 3 };
  assert.equal(matchCount(search, 'ISSUE'), 18231);
  assert.equal(matchCount(search, 'REPOSITORY'), 12);
  assert.equal(matchCount(search, 'USER'), 3);
  assert.equal(matchCount(null, 'ISSUE'), 0);
});

test('the run says the most it can spend', () => {
  assert.equal(pointCost(11), 11);
  assert.equal(pointCost(0), 0);
  assert.equal(pointCost(null), 0);
});

test('the document this script sends is a read', () => {
  assert.deepEqual(
    operations('query($q: String!) { search(query: $q, type: ISSUE, first: 100) { issueCount } }'),
    ['query'],
  );
  assert.ok(refusal('mutation M { addStar(input: {}) { clientMutationId } }'));
  assert.ok(refusal('subscription S { thing { id } }'));
  assert.equal(refusal(''), 'the document contains no operation to send.');
});
