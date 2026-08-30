import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  POINTS_PER_QUERY, auditable, classify, connectionFields, followupQueries,
  isConnection, missing, operations, outerText, pointCost, refusal, repair,
  resumable, truncated, unauditable, unresumable, walkConnections,
} from './github-graphql-nested.mjs';

const DATA = {
  repositoryOwner: {
    repositories: {
      totalCount: 218,
      pageInfo: { hasNextPage: true, endCursor: 'Y3Vyc29yOjU=' },
      nodes: [
        { name: 'monorepo', issues: { totalCount: 406, nodes: [{ number: 1 }, { number: 2 }] } },
        { name: 'tiny', issues: { totalCount: 2, nodes: [{ number: 9 }, { number: 10 }] } },
      ],
    },
  },
};

const NESTED_QUERY = 'query { repositoryOwner(login: "acme") {'
  + ' repositories(first: 5) { totalCount'
  + ' pageInfo { hasNextPage endCursor }'
  + ' nodes { name issues(first: 5) { totalCount nodes { number } } } } } }';

test('the walk finds inner connections by path', () => {
  const paths = walkConnections(DATA).map((e) => e.path);
  assert.ok(paths.includes('repositoryOwner.repositories'));
  assert.ok(paths.includes('repositoryOwner.repositories.nodes[0].issues'));
  assert.ok(paths.includes('repositoryOwner.repositories.nodes[1].issues'));
});

test('an inner connection is deeper than the one containing it', () => {
  const byPath = Object.fromEntries(walkConnections(DATA).map((e) => [e.path, e]));
  assert.equal(byPath['repositoryOwner.repositories'].depth, 0);
  assert.equal(byPath['repositoryOwner.repositories.nodes[0].issues'].depth, 1);
});

test('truncation is measured per parent and not in total', () => {
  const byPath = Object.fromEntries(walkConnections(DATA).map((e) => [e.path, e]));
  const big = byPath['repositoryOwner.repositories.nodes[0].issues'];
  const small = byPath['repositoryOwner.repositories.nodes[1].issues'];
  assert.equal(missing(big), 404);
  assert.ok(truncated(big));
  assert.equal(missing(small), 0);
  assert.ok(!truncated(small));
});

test('has next page alone is enough to call it truncated', () => {
  const entry = {
    depth: 1, returned: 100, total_count: null, has_next_page: true, end_cursor: 'abc',
  };
  assert.ok(truncated(entry));
  assert.equal(missing(entry), null);
  assert.ok(auditable(entry));
});

test('a connection with neither field cannot be judged at all', () => {
  const entry = {
    depth: 1, returned: 100, total_count: null, has_next_page: null, end_cursor: null,
  };
  assert.ok(!auditable(entry));
  assert.ok(!truncated(entry));
  const [state, detail] = classify([{
    depth: 0, returned: 5, total_count: 5, has_next_page: false, end_cursor: null,
  }, entry]);
  assert.equal(state, 'inner-connection-unauditable');
  assert.match(detail, /neither totalCount nor pageInfo/);
});

test('seeing the gap and being able to resume it are different', () => {
  const seenOnly = {
    depth: 1, returned: 5, total_count: 406, has_next_page: null, end_cursor: null,
  };
  assert.ok(truncated(seenOnly));
  assert.ok(!resumable(seenOnly));
  assert.ok(resumable({
    depth: 1, returned: 5, total_count: 406, has_next_page: true, end_cursor: 'abc',
  }));
});

test('the inner truncation outranks the outer one', () => {
  const [state, detail] = classify(walkConnections(DATA));
  assert.equal(state, 'inner-connection-truncated');
  assert.match(detail, /404/);
  assert.match(repair(state), /after: endCursor/);
});

test('an outer only truncation is named as the one people notice', () => {
  const data = {
    repositories: {
      totalCount: 218,
      pageInfo: { hasNextPage: true, endCursor: 'c' },
      nodes: [{ name: 'tiny', issues: { totalCount: 2, nodes: [{ number: 1 }, { number: 2 }] } }],
    },
  };
  const [state, detail] = classify(walkConnections(data));
  assert.equal(state, 'outer-connection-truncated');
  assert.match(detail, /do notice/);
});

test('a complete response is not reported as a finding', () => {
  const data = {
    repositories: {
      totalCount: 1,
      pageInfo: { hasNextPage: false, endCursor: null },
      nodes: [{ name: 'tiny', issues: { totalCount: 1, nodes: [{ number: 1 }] } }],
    },
  };
  assert.equal(classify(walkConnections(data))[0], 'complete');
  assert.equal(classify([])[0], 'no-connection-in-the-response');
});

test('an inner page info is never credited to its parent', () => {
  const doc = 'query { a(first: 10) { totalCount nodes {'
    + ' b(first: 10) { pageInfo { hasNextPage } nodes { id } } } } }';
  const fields = Object.fromEntries(connectionFields(doc).map((f) => [f.field, f]));
  assert.ok(fields.a.has_total_count && !fields.a.has_page_info);
  assert.ok(fields.b.has_page_info && !fields.b.has_total_count);
  assert.equal(fields.a.depth, 0);
  assert.equal(fields.b.depth, 1);
  assert.ok(!outerText(' totalCount nodes { pageInfo { x } } ').includes('pageInfo'));
});

test('the document audit names what cannot be checked or resumed', () => {
  const fields = connectionFields(NESTED_QUERY);
  assert.deepEqual(unresumable(fields).map((f) => f.field), ['issues']);
  assert.deepEqual(unauditable(fields), []);
  const bare = 'query { a(first: 5) { nodes { b(first: 5) { nodes { id } } } } }';
  assert.deepEqual(unauditable(connectionFields(bare)).map((f) => f.field), ['b']);
});

test('a connection is recognised by nodes or edges', () => {
  assert.ok(isConnection({ nodes: [] }));
  assert.ok(isConnection({ edges: [] }));
  assert.ok(!isConnection({ nodes: 3 }));
  assert.ok(!isConnection({ name: 'monorepo' }));
});

test('the cost of doing it properly is counted before the loop is written', () => {
  assert.equal(followupQueries(walkConnections(DATA)), 202);
  assert.equal(followupQueries([]), 0);
  assert.equal(POINTS_PER_QUERY, 1);
  assert.equal(pointCost(1), 1);
  assert.equal(pointCost(0), 0);
});

test('the script refuses to send a mutation', () => {
  assert.deepEqual(operations('query Q { viewer { login } }'), ['query']);
  assert.ok(refusal('mutation M { addStar(input: {}) { clientMutationId } }'));
  assert.ok(refusal('subscription S { thing { id } }'));
  assert.equal(refusal(NESTED_QUERY), null);
});
