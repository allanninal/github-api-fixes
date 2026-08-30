import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  PROBE_QUERY, SECONDARY_POINTS_PER_MINUTE, WEIGHT_WITHOUT_MUTATION,
  WEIGHT_WITH_MUTATION, ceilingPerMinute, classifyRate, classifyThrottle,
  minGapSeconds, minutesForBatch, operations, pointsPerMinute, price, refusal,
  repair, weight,
} from './github-graphql-mutation-budget.mjs';

const READ = 'query Q($n: Int!) { repository(owner: "a", name: "b") { issues(first: $n) { nodes { id } } } }';
const WRITE = 'mutation M($id: ID!) { addLabelsToLabelable(input: {labelableId: $id, labelIds: []}) { clientMutationId } }';
const THREE_WRITES = 'mutation A { one { clientMutationId } } '
  + 'mutation B { two { clientMutationId } } '
  + 'mutation C { three { clientMutationId } }';

test('a mutation document is five points and a query is one', () => {
  assert.equal(weight(WRITE), WEIGHT_WITH_MUTATION);
  assert.equal(WEIGHT_WITH_MUTATION, 5);
  assert.equal(weight(READ), WEIGHT_WITHOUT_MUTATION);
  assert.equal(WEIGHT_WITHOUT_MUTATION, 1);
});

test('the weight is per request not per mutation', () => {
  assert.deepEqual(operations(THREE_WRITES), ['mutation', 'mutation', 'mutation']);
  assert.equal(weight(THREE_WRITES), 5);
  assert.ok(weight(WRITE) * 3 > weight(THREE_WRITES));
});

test('the word mutation in a string or a comment is not one', () => {
  const quoted = 'query Q { search(query: "mutation", type: ISSUE, first: 1) { issueCount } }';
  assert.equal(weight(quoted), 1);
  assert.equal(refusal(quoted), null);
  const commented = '# mutation M { addStar }\nquery Q { viewer { login } }';
  assert.equal(weight(commented), 1);
  assert.equal(refusal(commented), null);
});

test('the ceiling is the limit divided by the weight', () => {
  assert.equal(SECONDARY_POINTS_PER_MINUTE, 2000);
  assert.equal(ceilingPerMinute(5), 400);
  assert.equal(ceilingPerMinute(1), 2000);
  assert.equal(ceilingPerMinute(0), 0);
  assert.equal(ceilingPerMinute(null), 0);
});

test('the gap falls out of the ceiling', () => {
  assert.equal(Number(minGapSeconds(5).toFixed(3)), 0.15);
  assert.equal(Number(minGapSeconds(1).toFixed(3)), 0.03);
  assert.equal(minGapSeconds(0), 0);
});

test('a rate is priced in points not in requests', () => {
  assert.equal(pointsPerMinute(500, 5), 2500);
  assert.equal(pointsPerMinute(500, 1), 500);
  assert.equal(pointsPerMinute(0, 5), 0);
  assert.equal(pointsPerMinute(null, 5), 0);
});

test('the same rate breaks the writer and not the reader', () => {
  assert.equal(classifyRate(500, weight(WRITE))[0], 'over-ceiling');
  assert.equal(classifyRate(500, weight(READ))[0], 'within-ceiling');
});

test('a rate just inside the limit is still reported', () => {
  const [state, detail] = classifyRate(340, 5);
  assert.equal(state, 'near-ceiling');
  assert.match(detail, /1700/);
  assert.match(repair(state), /headroom/);
});

test('an unmeasured rate is priced but not judged', () => {
  const [state, detail] = classifyRate(0, 5);
  assert.equal(state, 'not-measured');
  assert.match(detail, /400/);
});

test('a secondary message with a healthy budget is the finding', () => {
  const [state, detail] = classifyThrottle(
    403, 'You have exceeded a secondary rate limit', 4863);
  assert.equal(state, 'secondary-not-budget');
  assert.match(detail, /4863/);
  assert.match(repair(state), /points a minute/);
});

test('a secondary message with an empty budget is not conclusive', () => {
  assert.equal(
    classifyThrottle(429, 'You have exceeded a secondary rate limit', 0)[0],
    'secondary-limit',
  );
});

test('an exhausted hourly budget is handed to the other note', () => {
  const [state] = classifyThrottle(200, 'API rate limit exceeded', 0);
  assert.equal(state, 'primary-exhausted');
  assert.match(repair(state), /graphql-rate-limited/);
});

test('a 403 that is not a throttle is not called one', () => {
  assert.equal(classifyThrottle(403, 'Resource not accessible', 4900)[0],
    'forbidden-not-throttled');
  assert.equal(classifyThrottle('', '', 4900)[0], 'no-throttle');
});

test('a batch is costed in minutes', () => {
  assert.equal(minutesForBatch(11000, 400), 28);
  assert.equal(minutesForBatch(11000, 2000), 6);
  assert.equal(minutesForBatch(11000, 0), null);
});

test('the document is priced and refused in the same breath', () => {
  const p = price('label_issue.graphql', WRITE, 500);
  assert.equal(p.points_per_request, 5);
  assert.equal(p.ceiling_per_minute, 400);
  assert.equal(p.state, 'over-ceiling');
  assert.match(p.not_sent, /does not send them/);
  assert.equal(price('fetch.graphql', READ, 500).not_sent, null);
});

test('the scripts own probe passes its own guard', () => {
  assert.equal(refusal(PROBE_QUERY), null);
  assert.equal(weight(PROBE_QUERY), 1);
  assert.ok(refusal('subscription S { thing { id } }'));
  assert.equal(refusal(''), 'the document contains no operation to send.');
});
