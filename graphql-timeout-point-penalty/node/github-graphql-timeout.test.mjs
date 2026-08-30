import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  NEAR_LIMIT, POINTS_PER_QUERY, TIMEOUT_SECONDS, bucketReading, charged,
  classify, headroom, looksLikeTimeout, netCharge, operations, penalty,
  pointCost, refusal, repair, retryProjection, timeoutMessage, timingConsistent,
} from './github-graphql-timeout.mjs';

const PAYLOAD = {
  resources: {
    core: {
      limit: 5000, used: 900, remaining: 4100, reset: 1780000000,
    },
    graphql: {
      limit: 5000, used: 1204, remaining: 3796, reset: 1780000000,
    },
  },
};

const TIMED_OUT = {
  errors: [{
    message: 'Something went wrong while executing your query. This may be the '
      + 'result of a timeout',
  }],
};
const BEFORE = {
  limit: 5000, used: 1204, remaining: 3796, reset: 1780000000,
};
const AFTER = {
  limit: 5000, used: 1225, remaining: 3775, reset: 1780000000,
};

test('the reading comes from the graphql bucket and not core', () => {
  assert.equal(bucketReading(PAYLOAD).used, 1204);
  assert.equal(bucketReading(PAYLOAD, 'core').used, 900);
  assert.equal(bucketReading({}), null);
  assert.equal(bucketReading(null), null);
});

test('the charge is a subtraction over one window', () => {
  assert.deepEqual(charged(BEFORE, AFTER), [21, 'measured']);
  assert.deepEqual(charged(BEFORE, BEFORE), [0, 'measured']);
});

test('a window that reset between readings voids the measurement', () => {
  assert.deepEqual(charged(BEFORE, { ...AFTER, reset: 1780003600, used: 3 }),
    [null, 'window-reset']);
  assert.deepEqual(charged(BEFORE, { ...AFTER, used: 1100 }), [null, 'window-reset']);
  assert.deepEqual(charged(BEFORE, null), [null, 'unreadable']);
  assert.deepEqual(charged({ used: 'many', reset: 1 }, { used: 2, reset: 1 }),
    [null, 'unreadable']);
});

test('a known background drain is subtracted rather than ignored', () => {
  assert.equal(netCharge(21, 0), 21);
  assert.equal(netCharge(21, 5), 16);
  assert.equal(netCharge(3, 9), 0);
  assert.equal(netCharge(null, 0), null);
});

test('a timeout is recognised by status or by message', () => {
  assert.ok(looksLikeTimeout(502, null));
  assert.ok(looksLikeTimeout(504, null));
  assert.ok(looksLikeTimeout(200, TIMED_OUT));
  assert.ok(!looksLikeTimeout(200, { data: { viewer: { login: 'x' } } }));
  assert.ok(timeoutMessage(TIMED_OUT).startsWith('Something went wrong'));
  assert.equal(timeoutMessage({ message: 'Bad gateway' }), 'Bad gateway');
  assert.equal(timeoutMessage(null), null);
});

test('the clock is checked against the documented cutoff', () => {
  assert.equal(TIMEOUT_SECONDS, 10);
  assert.ok(timingConsistent(10.4));
  assert.ok(timingConsistent(8.0));
  assert.ok(!timingConsistent(3.2));
  assert.ok(!timingConsistent(null));
  assert.equal(Number(headroom(8.0).toFixed(2)), 0.8);
  assert.equal(headroom(-1), null);
});

test('a timeout charged above its normal cost is the headline', () => {
  const [state, detail] = classify(502, 10.4, 21, 12, 0, null);
  assert.equal(state, 'timed-out-and-charged-extra');
  assert.match(detail, /penalty of 9 point\(s\)/);
  assert.equal(penalty(21, 12), 9);
  assert.match(repair(state), /not retry/);
});

test('a timeout that did not prove the penalty says so', () => {
  const [state, detail] = classify(504, 10.1, 12, 12, 0, null);
  assert.equal(state, 'timed-out-charge-not-proved');
  assert.match(detail, /not demonstrated by this run/);
  assert.match(repair(state), /smaller anyway/);
});

test('a bucket that was already draining makes the charge unattributable', () => {
  const [state, detail] = classify(502, 10.2, 40, 12, 7, null);
  assert.equal(state, 'timed-out-charge-not-attributable');
  assert.match(detail, /belongs to more than this call/);
  assert.match(repair(state), /its own token/);
});

test('an unmeasurable charge is never reported as zero', () => {
  const [state, detail] = classify(502, 10.2, null, 12, 0, null);
  assert.equal(state, 'charge-not-measurable');
  assert.match(detail, /did time out/);
});

test('a successful call near the cutoff is still a finding', () => {
  const [state, detail] = classify(200, 8.2, 12, 12, 0, { data: { x: 1 } });
  assert.equal(state, 'close-to-the-timeout');
  assert.match(detail, /82%/);
  assert.equal(NEAR_LIMIT, 0.7);
  assert.match(repair(state), /rather than after the outage/);
});

test('an ordinary call is not dressed up as a problem', () => {
  const [state, detail] = classify(200, 3.4, 12, 12, 0, { data: { x: 1 } });
  assert.equal(state, 'completed-inside-the-limit');
  assert.match(detail, /ordinary case/);
});

test('the retry loop is priced but never run', () => {
  assert.equal(retryProjection(21, 3), 63);
  assert.equal(retryProjection(21, 0), 0);
  assert.equal(retryProjection(null, 3), 0);
});

test('the script refuses to send a mutation', () => {
  assert.deepEqual(operations('query Q { viewer { login } }'), ['query']);
  assert.ok(refusal('mutation M { addStar(input: {}) { clientMutationId } }'));
  assert.ok(refusal('subscription S { thing { id } }'));
  assert.equal(refusal('query Q { viewer { login } }'), null);
});

test('the run says what it will spend before the penalty', () => {
  assert.equal(POINTS_PER_QUERY, 1);
  assert.equal(pointCost(1), 1);
  assert.equal(pointCost(0), 0);
});
