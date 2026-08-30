import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  TIGHT, bucket, classify, fmtReset, identifyBudget, inBandCost, isRateLimited,
  operations, pointCost, queriesLeft, refusal, repair, secondsBetween,
  secondsToReset, sustainableRate, usedFraction,
} from './github-graphql-points.mjs';

function rl(coreRemaining, graphqlRemaining, coreLimit = 5000, graphqlLimit = 5000) {
  return {
    resources: {
      core: {
        limit: coreLimit,
        remaining: coreRemaining,
        used: coreLimit - coreRemaining,
        reset: 1800000000,
      },
      graphql: {
        limit: graphqlLimit,
        remaining: graphqlRemaining,
        used: graphqlLimit - graphqlRemaining,
        reset: 1800000000,
      },
    },
  };
}

test('the two buckets are read separately', () => {
  const body = rl(4983, 0);
  assert.equal(bucket(body, 'core').remaining, 4983);
  assert.equal(bucket(body, 'graphql').remaining, 0);
  assert.equal(bucket(body, 'search'), null);
  assert.equal(bucket({}, 'graphql'), null);
});

test('an empty GraphQL bucket beside a healthy core is the headline', () => {
  const body = rl(4983, 0);
  const [state, detail] = classify(bucket(body, 'graphql'), bucket(body, 'core'));
  assert.equal(state, 'graphql-exhausted-rest-healthy');
  assert.match(detail, /health check reports green/);
  assert.match(repair(state), /resources\.graphql\.remaining/);
});

test('an empty core beside a healthy GraphQL belongs to another note', () => {
  const body = rl(0, 4983);
  const [state] = classify(bucket(body, 'graphql'), bucket(body, 'core'));
  assert.equal(state, 'rest-exhausted-graphql-healthy');
  assert.match(repair(state), /rate-limit-core-exhausted/);
  assert.match(repair(state), /point budgeting/);
});

test('both empty is not the confusing case', () => {
  const body = rl(0, 0);
  const [state, detail] = classify(bucket(body, 'graphql'), bucket(body, 'core'));
  assert.equal(state, 'both-exhausted');
  assert.match(detail, /not the confusing case/);
});

test('a tight budget is flagged before it reaches zero', () => {
  const body = rl(4983, 500);
  assert.equal(classify(bucket(body, 'graphql'), bucket(body, 'core'))[0], 'graphql-tight');
  assert.equal(TIGHT, 0.2);
  const healthy = rl(4983, 4000);
  assert.equal(
    classify(bucket(healthy, 'graphql'), bucket(healthy, 'core'))[0],
    'both-healthy',
  );
});

test('a missing GraphQL bucket is reported rather than assumed full', () => {
  const [state] = classify(null, { limit: 5000, remaining: 4983 });
  assert.equal(state, 'unreadable');
});

test('usedFraction survives a bucket that makes no sense', () => {
  assert.equal(usedFraction({ limit: 5000, remaining: 2500 }), 0.5);
  assert.equal(usedFraction({ limit: 0, remaining: 0 }), null);
  assert.equal(usedFraction({ limit: 'many', remaining: 1 }), null);
  assert.equal(usedFraction(null), null);
});

test('points are converted into the unit you can schedule', () => {
  assert.equal(sustainableRate(5000, 12), 416);
  assert.equal(secondsBetween(5000, 12), 8.7);
  assert.equal(queriesLeft(1200, 12), 100);
  assert.equal(queriesLeft(11, 12), 0);
});

test('the conversion refuses a cost that cannot be divided by', () => {
  assert.equal(sustainableRate(5000, 0), null);
  assert.equal(queriesLeft(1200, 0), null);
  assert.equal(secondsBetween(5000, 'free'), null);
});

test('an observed limit names the actor it belongs to', () => {
  assert.equal(identifyBudget(5000), 'a user token');
  assert.match(identifyBudget(1000), /GitHub Actions/);
  assert.match(identifyBudget(10000), /Enterprise Cloud/);
  assert.match(identifyBudget(2500), /matches none of the published budgets/);
});

test('the error type is read from the envelope, not the status', () => {
  assert.ok(isRateLimited({ errors: [{ type: 'RATE_LIMITED' }] }));
  assert.ok(!isRateLimited({ errors: [{ type: 'NOT_FOUND' }] }));
  assert.ok(!isRateLimited({ data: { rateLimit: { cost: 1 } } }));
  assert.ok(!isRateLimited(null));
});

test('the in-band cost is read only when it was asked for', () => {
  assert.equal(inBandCost({ data: { rateLimit: { cost: 12, remaining: 4988 } } }), 12);
  assert.equal(inBandCost({ data: { viewer: { login: 'ada' } } }), null);
  assert.equal(inBandCost({ data: null }), null);
});

test('the reset delay is readable and never negative', () => {
  assert.equal(secondsToReset({ reset: 1000 }, 940), 60);
  assert.equal(secondsToReset({ reset: 1000 }, 2000), 0);
  assert.equal(secondsToReset({}, 100), null);
  assert.equal(fmtReset(45), '45s');
  assert.equal(fmtReset(720), '12m');
  assert.equal(fmtReset(null), 'unknown');
});

test('the default run spends nothing', () => {
  assert.equal(pointCost(false), 0);
  assert.equal(pointCost(true), 1);
});

test('the script refuses to send a mutation', () => {
  assert.deepEqual(operations('query { rateLimit { cost } }'), ['query']);
  assert.ok(refusal('mutation M { addStar(input: {}) { clientMutationId } }'));
  assert.equal(refusal('query { rateLimit { cost remaining } }'), null);
});
