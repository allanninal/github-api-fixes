import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  absent, classify, errorPaths, isPartialSuccess, nullPaths, operations,
  orphanErrorPaths, pathKey, permissionHint, pointCost, refusal, repair,
  safeToAggregate, tally, unpathedErrors, valueAt, withheld,
} from './github-graphql-partial.mjs';

const PARTIAL = {
  data: {
    repository: {
      name: 'monorepo',
      isPrivate: true,
      diskUsage: null,
      licenseInfo: null,
      collaborators: null,
    },
  },
  errors: [
    { type: 'FORBIDDEN', path: ['repository', 'diskUsage'] },
    { type: 'FORBIDDEN', path: ['repository', 'collaborators'] },
  ],
};

const IN_A_LIST = {
  data: {
    repository: {
      pullRequests: {
        nodes: [
          { number: 1, author: { login: 'ada' } },
          { number: 2, author: null },
        ],
      },
    },
  },
  errors: [{ type: 'FORBIDDEN', path: ['repository', 'pullRequests', 'nodes', 1, 'author'] }],
};

const TOTAL_FAILURE = {
  data: { repository: null },
  errors: [{ type: 'NOT_FOUND', path: ['repository'] }],
};

const CLEAN = { data: { repository: { name: 'monorepo', isPrivate: false } } };

test('a partial response is a third outcome, not a failure', () => {
  assert.ok(isPartialSuccess(PARTIAL));
  assert.ok(!isPartialSuccess(TOTAL_FAILURE));
  assert.ok(!isPartialSuccess(CLEAN));
});

test('withheld and absent are the two kinds of null', () => {
  assert.deepEqual(withheld(PARTIAL), ['repository.collaborators', 'repository.diskUsage']);
  assert.deepEqual(absent(PARTIAL), ['repository.licenseInfo']);
});

test('a null with no errors entry is a real answer', () => {
  const body = { data: { repository: { name: 'x', licenseInfo: null } } };
  assert.deepEqual(withheld(body), []);
  assert.deepEqual(absent(body), ['repository.licenseInfo']);
  const [state, detail] = classify(body);
  assert.equal(state, 'nulls-unexplained');
  assert.match(detail, /genuinely empty/);
});

test('error paths survive a list index', () => {
  assert.equal(
    pathKey(['repository', 'pullRequests', 'nodes', 1, 'author']),
    'repository.pullRequests.nodes.1.author',
  );
  assert.deepEqual(withheld(IN_A_LIST), ['repository.pullRequests.nodes.1.author']);
  assert.deepEqual(absent(IN_A_LIST), []);
});

test('the path resolver walks lists as well as objects', () => {
  const data = IN_A_LIST.data;
  assert.equal(valueAt(data, 'repository.pullRequests.nodes.0.number'), 1);
  assert.equal(valueAt(data, 'repository.pullRequests.nodes.1.author'), null);
  assert.deepEqual(nullPaths({ a: null, b: { c: null, d: 1 } }), ['a', 'b.c']);
});

test('an error path that matches no null is reported, not swallowed', () => {
  const body = {
    data: { repository: { name: 'x' } },
    errors: [{ type: 'FORBIDDEN', path: ['repository', 'gone'] }],
  };
  assert.deepEqual(orphanErrorPaths(body), ['repository.gone']);
  assert.deepEqual(withheld(body), []);
});

test('an error with no path cannot be attributed', () => {
  const body = {
    data: { repository: { name: 'x' } },
    errors: [{ type: 'INTERNAL', message: 'something broke' }],
  };
  assert.equal(unpathedErrors(body), 1);
  assert.deepEqual(errorPaths(body), {});
  const [state] = classify(body);
  assert.equal(state, 'errors-without-path');
  assert.match(repair(state), /verbatim/);
});

test('a query where nothing resolved belongs to the other note', () => {
  const [state, detail] = classify(TOTAL_FAILURE);
  assert.equal(state, 'total-failure');
  assert.match(detail, /failed query wearing a 200/);
  assert.match(repair(state), /graphql-200-with-errors/);
});

test('the finding names the paths rather than counting errors', () => {
  const [state, detail] = classify(PARTIAL);
  assert.equal(state, 'partial-withheld');
  assert.match(detail, /errors\[\]\.path/);
  assert.match(repair(state), /unknown, not zero/);
  assert.match(repair(state), /Do not retry/);
});

test('an aggregate over a root with withheld fields is a lower bound', () => {
  const [ok, sentence] = safeToAggregate(PARTIAL, 'repository');
  assert.ok(!ok);
  assert.match(sentence, /lower bound/);
  const [ok2, sentence2] = safeToAggregate(PARTIAL, 'viewer');
  assert.ok(ok2);
  assert.match(sentence2, /is a total/);
});

test('the aggregation root is matched on a boundary, not a prefix', () => {
  const body = {
    data: { repo: { a: null }, repository: { b: 1 } },
    errors: [{ type: 'FORBIDDEN', path: ['repo', 'a'] }],
  };
  const [ok] = safeToAggregate(body, 'repository');
  assert.ok(ok);
});

test('each withheld field names the permission it would want', () => {
  assert.match(permissionHint('repository.diskUsage'), /admin/);
  assert.match(permissionHint('repository.collaborators'), /members/);
  assert.equal(
    permissionHint('repository.somethingNew'),
    'the permission that covers this field',
  );
});

test('the tally counts all four kinds of thing', () => {
  assert.deepEqual(tally(PARTIAL),
    { withheld: 2, absent: 1, orphaned: 0, unpathed_errors: 0 });
  assert.deepEqual(tally(CLEAN),
    { withheld: 0, absent: 0, orphaned: 0, unpathed_errors: 0 });
});

test('a clean response says so plainly', () => {
  const [state] = classify(CLEAN);
  assert.equal(state, 'complete');
  assert.equal(repair(state), 'nothing.');
  assert.equal(classify(null)[0], 'unreadable');
});

test('the script refuses to send a mutation', () => {
  assert.deepEqual(operations('query Q { viewer { login } }'), ['query']);
  assert.ok(refusal('mutation M { addStar(input: {}) { clientMutationId } }'));
  assert.ok(refusal('subscription S { thing { id } }'));
  assert.equal(refusal('query Q { repository(owner: "o", name: "n") { name } }'), null);
});

test('the run says what it will spend', () => {
  assert.equal(pointCost(1), 1);
  assert.equal(pointCost(0), 0);
  assert.equal(pointCost(null), 0);
});
