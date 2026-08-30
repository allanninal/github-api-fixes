import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  NODE_LIMIT, caveats, commas, connections, deepest, exceeds, fragmentSpreads,
  nodeCount, operations, pointCost, refusal, rejectedForNodes, repair,
  reportedNodeCount, reshape, unresolved, verdict,
} from './github-graphql-nodes.mjs';

const THREE_LEVELS = `query {
  organization(login: "acme") {
    repositories(first: 100) {
      nodes {
        pullRequests(first: 100) {
          nodes {
            comments(first: 100) { nodes { id } }
          }
        }
      }
    }
  }
}`;

const SMALL = `query {
  organization(login: "acme") {
    repositories(first: 100) {
      nodes { pullRequests(first: 10) { nodes { number } } }
    }
  }
}`;

test('the canonical query is the documented million', () => {
  assert.equal(nodeCount(THREE_LEVELS), 1010100);
  assert.ok(exceeds(nodeCount(THREE_LEVELS)));
  assert.equal(NODE_LIMIT, 500000);
});

test('the multiplier chain is what makes it large', () => {
  const byField = Object.fromEntries(connections(THREE_LEVELS).map((c) => [c.field, c]));
  assert.equal(byField.repositories.ancestors, 1);
  assert.equal(byField.repositories.nodes, 100);
  assert.equal(byField.pullRequests.ancestors, 100);
  assert.equal(byField.pullRequests.nodes, 10000);
  assert.equal(byField.comments.ancestors, 10000);
  assert.equal(byField.comments.nodes, 1000000);
});

test('the deepest connection carries almost all of it', () => {
  const d = deepest(THREE_LEVELS);
  assert.equal(d.field, 'comments');
  assert.equal(d.nodes, 1000000);
});

test('the repair is a number and the number fits', () => {
  const [field, current, suggested] = reshape(THREE_LEVELS);
  assert.equal(field, 'comments');
  assert.equal(current, 100);
  assert.equal(suggested, 48);
  const fixed = THREE_LEVELS.replace('comments(first: 100)', 'comments(first: 48)');
  assert.equal(nodeCount(fixed), 490100);
  assert.ok(!exceeds(nodeCount(fixed)));
});

test('a query that cannot be rescued by one number says so', () => {
  const huge = 'query { a(first: 100) { nodes { b(first: 100) { nodes { '
    + 'c(first: 100) { nodes { d(first: 100) { nodes { id } } } } } } } } }';
  assert.ok(exceeds(nodeCount(huge)));
  const [field, , suggested] = reshape(huge);
  assert.equal(suggested, null);
  assert.match(repair('over-node-limit', field, 100, null), /split it into separate queries/);
});

test('a small query is not flagged', () => {
  assert.equal(nodeCount(SMALL), 1100);
  assert.equal(verdict(SMALL)[0], 'within-node-limit');
});

test('the verdict names the three bands', () => {
  assert.equal(verdict(THREE_LEVELS)[0], 'over-node-limit');
  const near = 'query { a(first: 100) { nodes { b(first: 4500) { nodes { id } } } } }';
  assert.equal(nodeCount(near), 450100);
  assert.equal(verdict(near)[0], 'near-node-limit');
  assert.equal(verdict(SMALL)[0], 'within-node-limit');
  assert.equal(verdict('query { viewer { login } }')[0], 'no-connections');
});

test('a slice supplied as a variable is resolved or reported', () => {
  const doc = 'query($n: Int!) { a(first: $n) { nodes { id } } }';
  assert.equal(nodeCount(doc, { n: 50 }), 50);
  assert.equal(verdict(doc, { n: 50 })[0], 'within-node-limit');
  assert.deepEqual(unresolved(doc), ['a']);
  assert.equal(verdict(doc)[0], 'unresolved-variables');
  assert.match(caveats(doc)[0], /GITHUB_VARIABLES/);
});

test('a directive does not erase the slice before it', () => {
  const doc = 'query($show: Boolean!) { repositories(first: 100) @include(if: $show) '
    + '{ nodes { id } } }';
  assert.equal(nodeCount(doc), 100);
});

test('a fragment spread makes the total a lower bound', () => {
  const doc = 'query { repositories(first: 100) { nodes { ...RepoBits } } } '
    + 'fragment RepoBits on Repository { pullRequests(first: 100) { nodes { id } } }';
  assert.deepEqual(fragmentSpreads(doc), ['RepoBits']);
  assert.ok(caveats(doc).some((c) => c.includes('lower bound')));
});

test('an inline fragment is not mistaken for a spread', () => {
  const doc = 'query { search(query: "x", type: ISSUE, first: 10) { nodes { ... on Issue { id } } } }';
  assert.deepEqual(fragmentSpreads(doc), []);
  assert.equal(nodeCount(doc), 10);
});

test('the word first inside a string is not a slice', () => {
  const doc = 'query { search(query: "first: 100", type: ISSUE, first: 5) { nodes { id } } }';
  assert.equal(nodeCount(doc), 5);
});

test('a comment is not read as part of the query', () => {
  const doc = '# repositories(first: 100)\nquery { a(first: 7) { nodes { id } } }';
  assert.equal(nodeCount(doc), 7);
});

test('the server can be asked to agree but does not have to be', () => {
  assert.ok(rejectedForNodes({ errors: [{ type: 'MAX_NODE_LIMIT_EXCEEDED' }] }));
  assert.ok(!rejectedForNodes({ errors: [{ type: 'RATE_LIMITED' }] }));
  assert.equal(reportedNodeCount({ data: { rateLimit: { nodeCount: 1100 } } }), 1100);
  assert.equal(reportedNodeCount({ data: { viewer: { login: 'ada' } } }), null);
});

test('counts are printed in something readable', () => {
  assert.equal(commas(1010100), '1,010,100');
  assert.equal(commas(100), '100');
  assert.equal(commas(null), 'null');
});

test('the default run spends nothing', () => {
  assert.equal(pointCost(false), 0);
  assert.equal(pointCost(true), 1);
});

test('the script refuses to analyse and send a mutation', () => {
  assert.deepEqual(operations(THREE_LEVELS), ['query']);
  assert.ok(refusal('mutation M { addStar(input: {}) { clientMutationId } }'));
  assert.equal(refusal(THREE_LEVELS), null);
});
