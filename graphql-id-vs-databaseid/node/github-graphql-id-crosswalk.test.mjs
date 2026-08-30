import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  classifyPair, classifyStore, crosswalk, decodeLegacyNodeId, idSpace, joinRows,
  joinRowsNormalised, migrationSplit, numberIsNotTheDatabaseId, operations,
  refusal, repair, toDatabaseId,
} from './github-graphql-id-crosswalk.mjs';

const REST_ISSUE = {
  id: 1347, node_id: 'MDU6SXNzdWUxMzQ3', number: 1347, title: 'Found a bug',
};
const GQL_ISSUE = { id: 'MDU6SXNzdWUxMzQ3', databaseId: 1347, number: 1347 };
const NEW_STYLE = 'I_kwDOAbCdEf4AbCdE';

test('a legacy node id carries the database id inside it', () => {
  assert.deepEqual(decodeLegacyNodeId('MDU6SXNzdWUxMzQ3'), ['Issue', 1347]);
  assert.deepEqual(decodeLegacyNodeId('MDU6SXNzdWUx'), ['Issue', 1]);
  assert.deepEqual(decodeLegacyNodeId('MDEwOlJlcG9zaXRvcnkxMjk2MjY5'),
    ['Repository', 1296269]);
});

test('the new format carries nothing and must be refetched', () => {
  assert.equal(decodeLegacyNodeId(NEW_STYLE), null);
  assert.equal(idSpace(NEW_STYLE), 'graphql-node-id');
  assert.equal(toDatabaseId(NEW_STYLE), null);
});

test('an ordinary string is not mistaken for an identifier', () => {
  assert.equal(decodeLegacyNodeId('aGVsbG8gd29ybGQ='), null);
  assert.equal(decodeLegacyNodeId('not base64 at all'), null);
  assert.equal(decodeLegacyNodeId(''), null);
  assert.equal(decodeLegacyNodeId('1347'), null);
});

test('each identifier is placed in exactly one key space', () => {
  assert.equal(idSpace(1347), 'rest-database-id');
  assert.equal(idSpace('1347'), 'rest-database-id');
  assert.equal(idSpace('MDU6SXNzdWUxMzQ3'), 'graphql-node-id');
  assert.equal(idSpace('acme/monorepo#1347'), 'unknown');
  assert.equal(idSpace(null), 'unknown');
  assert.equal(idSpace(true), 'unknown');
});

test('the crosswalk holds in both directions', () => {
  const facts = crosswalk(REST_ISSUE, GQL_ISSUE);
  assert.ok(facts.node_ids_match);
  assert.ok(facts.database_ids_match);
  const [state, detail] = classifyPair(REST_ISSUE, GQL_ISSUE);
  assert.equal(state, 'crosswalk-confirmed');
  assert.match(detail, /REST node_id equals GraphQL id/);
});

test('two different objects are not reported as a key problem', () => {
  const [state, detail] = classifyPair(REST_ISSUE,
    { id: 'MDU6SXNzdWUx', databaseId: 1 });
  assert.equal(state, 'crosswalk-broken');
  assert.match(detail, /not the same object/);
  assert.match(repair(state), /number is not its databaseId/);
});

test('a type with no database id has only one key', () => {
  const [state] = classifyPair(REST_ISSUE, { id: NEW_STYLE, databaseId: null });
  assert.equal(state, 'database-id-absent');
  assert.match(repair(state), /node ID/);
  assert.equal(classifyPair({}, {})[0], 'incomplete');
});

test('a column holding both spaces is the finding', () => {
  const [state, detail] = classifyStore(['1347', 'MDU6SXNzdWUxMzQ3', NEW_STYLE]);
  assert.equal(state, 'mixed-key-space');
  assert.match(detail, /1 database id\(s\)/);
  assert.match(detail, /2 node id\(s\)/);
  assert.match(repair(state), /pick one key space/);
});

test('a consistent column is left alone', () => {
  assert.equal(classifyStore(['1347', '1348'])[0], 'consistent-database-id');
  assert.equal(classifyStore(['MDU6SXNzdWUxMzQ3', NEW_STYLE])[0], 'consistent-node-id');
  assert.equal(classifyStore(['acme/monorepo#1'])[0], 'unrecognised');
  assert.equal(classifyStore([])[0], 'no-sample');
});

test('the join returns nothing across two key spaces', () => {
  const restSide = ['1347', '1348'];
  const graphqlSide = ['MDU6SXNzdWUxMzQ3', 'MDU6SXNzdWUxMzQ4'];
  assert.equal(joinRows(restSide, graphqlSide), 0);
  assert.equal(joinRowsNormalised(restSide, graphqlSide), 2);
});

test('normalising cannot rescue the new format', () => {
  assert.equal(joinRowsNormalised(['1347'], ['MDU6SXNzdWUxMzQ3']), 1);
  assert.equal(joinRowsNormalised(['1347'], [NEW_STYLE]), 0);
});

test('the migration is split into offline and refetch', () => {
  assert.deepEqual(migrationSplit(['1347', 'MDU6SXNzdWUxMzQ3', NEW_STYLE, 'junk']),
    { already_numeric: 1, decodable_offline: 1, needs_refetching: 1 });
});

test('the number is a third integer and not the database id', () => {
  const other = { id: 2136843289, node_id: 'MDU6SXNzdWUx', number: 1347 };
  assert.equal(numberIsNotTheDatabaseId(other), true);
  assert.equal(numberIsNotTheDatabaseId(REST_ISSUE), false);
  assert.equal(numberIsNotTheDatabaseId({}), null);
  assert.equal(idSpace(other.number), idSpace(other.id));
});

test('the document this script sends is a read', () => {
  assert.deepEqual(
    operations('query Q { repository(owner: "a", name: "b") { id databaseId } }'),
    ['query'],
  );
  assert.ok(refusal('mutation M { addStar(input: {}) { clientMutationId } }'));
  assert.ok(refusal('subscription S { thing { id } }'));
  assert.equal(refusal(''), 'the document contains no operation to send.');
});
