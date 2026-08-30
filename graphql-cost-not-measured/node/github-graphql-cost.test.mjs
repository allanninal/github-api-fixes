import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  POINTS_PER_QUERY, blankNoise, classify, drift, gap, injectRateLimit,
  measuredCost, measuredNodes, operations, pointCost, pointsPerHour,
  predictedCost, refusal, repair, returnedNodes, selectionSetStart, sliceValues,
} from './github-graphql-cost.mjs';

const QUERY = 'query($login: String!) { repositoryOwner(login: $login) {'
  + ' repositories(first: 50) { nodes { name'
  + ' issues(first: 20) { nodes { number } } } } } }';

const BODY = {
  data: {
    rateLimit: {
      cost: 14, nodeCount: 3180, limit: 5000, remaining: 4986,
    },
    repositoryOwner: {
      repositories: {
        nodes: [
          { name: 'a', issues: { nodes: [{ number: 1 }, { number: 2 }] } },
          { name: 'b', issues: { nodes: [{ number: 3 }] } },
        ],
      },
    },
  },
};

test('the injection lands in the operation selection set', () => {
  const out = injectRateLimit(QUERY);
  assert.ok(out.includes('rateLimit { cost nodeCount limit remaining resetAt }'));
  assert.ok(out.indexOf('repositoryOwner') > out.indexOf('rateLimit'));
  assert.equal(out.split('rateLimit').length - 1, 1);
});

test('a document that already asks for it is left alone', () => {
  const once = injectRateLimit(QUERY);
  assert.equal(injectRateLimit(once), once);
  const already = 'query { rateLimit { cost } viewer { login } }';
  assert.equal(injectRateLimit(already), already);
});

test('a brace in the variable definitions is not the selection set', () => {
  const doc = 'query($order: IssueOrder = {field: CREATED_AT}) { viewer { login } }';
  const at = selectionSetStart(doc);
  assert.equal(doc.slice(at, at + 3), '{ v');
  const out = injectRateLimit(doc);
  assert.ok(out.indexOf('IssueOrder') < out.indexOf('rateLimit'));
  assert.ok(out.indexOf('rateLimit') < out.indexOf('viewer'));
});

test('blanking the noise keeps every index where it was', () => {
  const doc = 'query { search(query: "a { b }", type: ISSUE, first: 5) { issueCount } }';
  const blanked = blankNoise(doc);
  assert.equal(blanked.length, doc.length);
  assert.ok(!blanked.includes('{ b }'));
  assert.equal(blanked.indexOf('issueCount'), doc.indexOf('issueCount'));
});

test('the prediction comes from the slices and never from zero', () => {
  assert.deepEqual(predictedCost(QUERY, {}), [1, 0]);
  const big = 'query { a(first: 100) { nodes { b(first: 100) { nodes { id } } } } }';
  assert.equal(predictedCost(big, {})[0], 2);
  assert.deepEqual(predictedCost('query { viewer { login } }', {}), [1, 0]);
});

test('an unresolved slice makes the prediction a lower bound', () => {
  const doc = 'query($n: Int!) { a(first: $n) { nodes { id } } }';
  const [points, unresolved] = predictedCost(doc, {});
  assert.equal(unresolved, 1);
  assert.equal(points, 1);
  assert.equal(predictedCost(doc, { n: 300 })[0], 3);
});

test('a variable definition is not counted as a slice', () => {
  const doc = 'query($first: Int = 250) { a(first: 10) { nodes { id } } }';
  assert.deepEqual(sliceValues(doc, {}).map((v) => [v.arg, v.value]), [['first', 10]]);
});

test('the server number is read out of the response wherever it sits', () => {
  assert.equal(measuredCost(BODY), 14);
  assert.equal(measuredNodes(BODY), 3180);
  assert.equal(measuredCost({ data: { viewer: { login: 'x' } } }), null);
  assert.equal(measuredCost(null), null);
});

test('the price is compared with the data that came back', () => {
  assert.equal(returnedNodes(BODY.data), 5);
  assert.equal(returnedNodes({ nodes: [1, 2, 3] }), 3);
  assert.equal(returnedNodes({ name: 'a' }), 0);
});

test('the gap between the text and the server is the finding', () => {
  assert.equal(gap(3, 14)[1], 'far-above-the-text');
  assert.equal(gap(4, 6)[1], 'above-the-text');
  assert.equal(gap(4, 4)[1], 'close-to-the-text');
  assert.equal(gap(10, 2)[1], 'below-the-text');
  assert.equal(gap(3, null)[1], 'unmeasured');
});

test('drift against a recorded baseline is reported as a percentage', () => {
  const [state, detail] = drift(3, 14);
  assert.equal(state, 'increased');
  assert.match(detail, /367%/);
  assert.equal(drift(3, 3)[0], 'unchanged');
  assert.equal(drift(14, 3)[0], 'decreased');
  assert.equal(drift(null, 14)[0], 'no-baseline');
});

test('a price rise outranks everything else because it is reviewable', () => {
  const [state, detail] = classify(14, 3, 3, 5);
  assert.equal(state, 'cost-increased-since-the-baseline');
  assert.match(detail, /367%/);
  assert.match(repair(state), /code review/);
});

test('a query costing more than its text suggests is named as that', () => {
  const [state, detail] = classify(14, 3, null, 5);
  assert.equal(state, 'cost-above-the-shape-of-the-query');
  assert.match(detail, /factor of 4\.7/);
});

test('cost not following the data is its own finding', () => {
  const [state, detail] = classify(9, 9, 9, 4);
  assert.equal(state, 'cost-unrelated-to-the-data-returned');
  assert.match(detail, /4 node\(s\) came back for 9 point\(s\)/);
  assert.match(repair(state), /Filters change/);
});

test('an unmeasured run says so rather than guessing', () => {
  const [state] = classify(null, 3, 3, 5);
  assert.equal(state, 'cost-unmeasured');
  assert.match(repair(state), /rateLimit \{ cost nodeCount remaining \}/);
});

test('the hourly projection is multiplication and nothing more', () => {
  assert.equal(pointsPerHour(14, 240), 3360);
  assert.equal(pointsPerHour(14, 0), null);
  assert.equal(pointsPerHour(null, 240), null);
});

test('the script refuses to send a mutation', () => {
  assert.deepEqual(operations('query Q { viewer { login } }'), ['query']);
  assert.ok(refusal('mutation M { addStar(input: {}) { clientMutationId } }'));
  assert.ok(refusal('subscription S { thing { id } }'));
  assert.equal(refusal(QUERY), null);
});

test('the run says what it will spend', () => {
  assert.equal(POINTS_PER_QUERY, 1);
  assert.equal(pointCost(1), 1);
  assert.equal(pointCost(0), 0);
});
