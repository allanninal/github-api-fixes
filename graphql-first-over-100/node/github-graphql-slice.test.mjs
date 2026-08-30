import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  CEILING, POINTS_PER_QUERY, argumentValue, audit, classify, errorPhase,
  offendingArgument, operations, pagesNeeded, pointCost, refusal, repair,
  slicingArguments, variableDefaults, verdict,
} from './github-graphql-slice.mjs';

const LITERAL = 'query { repository(owner: "a", name: "b") { issues(first: 500) { totalCount } } }';
const VIA_DEFAULT = 'query($first: Int = 250) { repository(owner: "a", name: "b")'
  + ' { issues(first: $first) { totalCount } } }';
const SAFE = 'query($first: Int = 100) { repository(owner: "a", name: "b")'
  + ' { issues(first: $first) { totalCount } } }';

const VALIDATION_BODY = {
  errors: [{ message: "Argument 'first' on Field 'issues' has an invalid value (500)." }],
};
const EXECUTION_BODY = {
  data: { repository: null },
  errors: [{ type: 'NOT_FOUND', message: 'Could not resolve' }],
};

test('the ceiling is one hundred everywhere', () => {
  assert.equal(CEILING, 100);
  assert.equal(verdict(101), 'over-ceiling');
  assert.equal(verdict(100), 'at-ceiling');
  assert.equal(verdict(1), 'under-ceiling');
  assert.equal(verdict(0), 'below-one');
  assert.equal(verdict(null), 'unresolved');
});

test('a literal over the ceiling is found in the text', () => {
  const found = audit(LITERAL, {});
  assert.deepEqual(found.map((f) => [f.field, f.arg, f.value, f.source]),
    [['issues', 'first', 500, 'literal']]);
  const [state, detail] = classify(found);
  assert.equal(state, 'over-ceiling-in-the-document');
  assert.match(detail, /500/);
});

test('a variable default over the ceiling is invisible to a grep', () => {
  const found = audit(VIA_DEFAULT, {});
  assert.ok(!found.map((f) => f.written).join('').includes('250'));
  assert.equal(found[0].value, 250);
  assert.equal(found[0].source, 'variable-default');
  const [state, detail] = classify(found);
  assert.equal(state, 'over-ceiling-through-a-variable');
  assert.match(detail, /finds nothing/);
});

test('a supplied variable beats the default because the server sees it', () => {
  const found = audit(SAFE, { first: 400 });
  assert.equal(found[0].value, 400);
  assert.equal(found[0].source, 'variable-supplied');
  assert.equal(classify(found)[0], 'over-ceiling-through-a-variable');
  assert.equal(classify(audit(SAFE, {}))[0], 'within-the-ceiling');
});

test('an unresolved variable is never assumed safe', () => {
  const doc = 'query($n: Int!) { repository(owner: "a", name: "b") { issues(first: $n) { totalCount } } }';
  const found = audit(doc, {});
  assert.equal(found[0].source, 'unresolved');
  assert.equal(found[0].verdict, 'unresolved');
  const state = classify(found)[0];
  assert.equal(state, 'unresolved-slice');
  assert.match(repair(state), /variables/);
});

test('a variable definition is not an argument called first', () => {
  assert.deepEqual(variableDefaults(VIA_DEFAULT), { $first: '250' });
  const args = slicingArguments(VIA_DEFAULT);
  assert.equal(args.length, 1);
  assert.equal(args[0].field, 'issues');
  assert.equal(argumentValue('$first: Int = 250', 'first'), null);
  assert.equal(argumentValue('first: 100, states: OPEN', 'first'), '100');
});

test('last is treated exactly like first', () => {
  const doc = 'query { repository(owner: "a", name: "b") { issues(last: 250) { totalCount } } }';
  const found = audit(doc, {});
  assert.equal(found[0].arg, 'last');
  assert.equal(classify(found)[0], 'over-ceiling-in-the-document');
});

test('the word first inside a string is not an argument', () => {
  const doc = 'query { search(query: "first: 500", type: ISSUE, first: 10) { issueCount } }';
  const found = audit(doc, {});
  assert.deepEqual(found.map((f) => [f.arg, f.value]), [['first', 10]]);
  assert.equal(classify(found)[0], 'within-the-ceiling');
});

test('a clean document is sent on to the node count rather than cleared', () => {
  const state = classify(audit(SAFE, {}))[0];
  assert.equal(state, 'within-the-ceiling');
  assert.match(repair(state), /graphql-node-limit-exceeded/);
});

test('the pages that number really means', () => {
  assert.equal(pagesNeeded(500), 5);
  assert.equal(pagesNeeded(101), 2);
  assert.equal(pagesNeeded(100), 1);
  assert.equal(pagesNeeded(0), null);
  assert.equal(pagesNeeded(null), null);
});

test('a validation failure carries no data key at all', () => {
  assert.equal(errorPhase(200, VALIDATION_BODY), 'validation');
  assert.equal(errorPhase(200, EXECUTION_BODY), 'execution');
  assert.equal(errorPhase(200, { data: { repository: { name: 'x' } } }), 'clean');
  assert.equal(errorPhase(200, null), 'unreadable');
});

test('the server names the argument and the field', () => {
  assert.deepEqual(offendingArgument(VALIDATION_BODY), ['first', 'issues']);
  assert.deepEqual(offendingArgument(EXECUTION_BODY), [null, null]);
  assert.deepEqual(offendingArgument(null), [null, null]);
});

test('the script refuses to send a mutation', () => {
  assert.deepEqual(operations('query Q { viewer { login } }'), ['query']);
  assert.ok(refusal('mutation M { addStar(input: {}) { clientMutationId } }'));
  assert.ok(refusal('subscription S { thing { id } }'));
  assert.equal(refusal(''), 'the document contains no operation to send.');
  assert.equal(refusal(LITERAL), null);
});

test('the offline audit spends nothing', () => {
  assert.equal(POINTS_PER_QUERY, 1);
  assert.equal(pointCost(false), 0);
  assert.equal(pointCost(true), 1);
});
