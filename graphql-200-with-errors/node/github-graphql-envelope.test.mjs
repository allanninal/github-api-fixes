import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  POINTS_PER_QUERY, behaviourFor, classify, envelopeSaysOk, errorTypes,
  hasUsableData, operations, pointCost, predicatesDisagree, refusal, repair,
  statusSaysOk,
} from './github-graphql-envelope.mjs';

const FAILED = {
  data: { repository: null },
  errors: [{ type: 'NOT_FOUND', message: 'Could not resolve to a Repository' }],
};
const PARTIAL = {
  data: { repository: { name: 'monorepo', diskUsage: null } },
  errors: [{ type: 'FORBIDDEN', path: ['repository', 'diskUsage'] }],
};
const CLEAN = { data: { repository: { name: 'monorepo' } } };

test('the status line says success on a failed query', () => {
  assert.ok(statusSaysOk(200));
  assert.ok(statusSaysOk('201'));
  assert.ok(!statusSaysOk(403));
  assert.ok(!statusSaysOk(null));
});

test('the envelope check reads the body instead', () => {
  assert.ok(!envelopeSaysOk(FAILED));
  assert.ok(!envelopeSaysOk(PARTIAL));
  assert.ok(envelopeSaysOk(CLEAN));
  assert.ok(envelopeSaysOk({ data: {}, errors: [] }));
  assert.ok(!envelopeSaysOk('not a body'));
});

test('the finding is exactly the disagreement', () => {
  assert.ok(predicatesDisagree(200, FAILED));
  assert.ok(predicatesDisagree(200, PARTIAL));
  assert.ok(!predicatesDisagree(200, CLEAN));
  assert.ok(!predicatesDisagree(502, FAILED));
});

test('error types survive an entry with no type', () => {
  assert.deepEqual(errorTypes(FAILED), ['NOT_FOUND']);
  assert.deepEqual(errorTypes({ errors: [{ message: 'boom' }] }), ['UNTYPED']);
  assert.deepEqual(errorTypes({ errors: ['a string'] }), ['UNTYPED']);
  assert.deepEqual(errorTypes(CLEAN), []);
});

test('usable data means at least one field resolved', () => {
  assert.ok(!hasUsableData(FAILED));
  assert.ok(hasUsableData(PARTIAL));
  assert.ok(hasUsableData(CLEAN));
  assert.ok(!hasUsableData({ data: null, errors: [{ type: 'RATE_LIMITED' }] }));
});

test('a 200 carrying errors and no data is the headline', () => {
  const [state, detail] = classify(200, FAILED);
  assert.equal(state, '200-with-errors-no-data');
  assert.match(detail, /NOT_FOUND/);
  assert.match(repair(state), /read body\.errors before body\.data/);
});

test('errors alongside real data are handed on rather than absorbed', () => {
  const [state, detail] = classify(200, PARTIAL);
  assert.equal(state, '200-with-errors-and-data');
  assert.match(detail, /partial success/);
  assert.match(repair(state), /graphql-partial-data-nulls/);
  assert.match(repair(state), /do not retry/);
});

test('a real transport failure is not this note', () => {
  const [state] = classify(502, { errors: [{ type: 'INTERNAL' }] });
  assert.equal(state, 'transport-failure');
  assert.match(repair(state), /status code as you already do/);
});

test('a clean response is not reported as proof of anything', () => {
  const [state, detail] = classify(200, CLEAN);
  assert.equal(state, '200-clean');
  assert.match(detail, /agreement rather than proof/);
});

test('an unreadable body is not reported as success', () => {
  assert.equal(classify(200, null)[0], 'unreadable');
  assert.equal(classify(200, [1, 2])[0], 'unreadable');
});

test('each error type gets its own behaviour', () => {
  assert.equal(behaviourFor('RATE_LIMITED')[0], 'wait');
  assert.equal(behaviourFor('FORBIDDEN')[0], 'alert');
  assert.equal(behaviourFor('NOT_FOUND')[0], 'record-absent');
  assert.equal(behaviourFor('MAX_NODE_LIMIT_EXCEEDED')[0], 'reshape');
  assert.equal(behaviourFor('INTERNAL')[0], 'retry-once');
});

test('a node limit error is never advised to retry', () => {
  const [action, detail] = behaviourFor('MAX_NODE_LIMIT_EXCEEDED');
  assert.equal(action, 'reshape');
  assert.match(detail, /fail identically every time/);
});

test('an unknown error type falls through rather than being guessed', () => {
  const [action, detail] = behaviourFor('SOMETHING_NEW_IN_2027');
  assert.equal(action, 'log-verbatim');
  assert.match(detail, /does not know/);
});

test('the script refuses to send a mutation', () => {
  assert.deepEqual(operations('query Q { viewer { login } }'), ['query']);
  assert.deepEqual(operations('{ viewer { login } }'), ['query']);
  assert.deepEqual(
    operations('mutation M { addStar(input: {}) { clientMutationId } }'),
    ['mutation'],
  );
  assert.ok(refusal('mutation M { addStar(input: {}) { clientMutationId } }'));
  assert.ok(refusal('subscription S { thing { id } }'));
  assert.equal(refusal(''), 'the document contains no operation to send.');
  assert.equal(refusal('query Q { viewer { login } }'), null);
});

test('the word mutation inside a string is not a mutation', () => {
  const doc = 'query Q { search(query: "mutation", type: ISSUE, first: 1) { issueCount } }';
  assert.deepEqual(operations(doc), ['query']);
  assert.equal(refusal(doc), null);
});

test('a commented out mutation is not sent and not feared', () => {
  const doc = '# mutation M { addStar }\nquery Q { viewer { login } }';
  assert.deepEqual(operations(doc), ['query']);
  assert.equal(refusal(doc), null);
});

test('the run says what it will spend', () => {
  assert.equal(POINTS_PER_QUERY, 1);
  assert.equal(pointCost([1, 2]), 2);
  assert.equal(pointCost([]), 0);
  assert.equal(pointCost(null), 0);
});
