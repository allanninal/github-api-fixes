/**
 * Prove the crosswalk between GraphQL node ids and REST database ids.
 *
 * Read only, and queries only. GitHub's GraphQL endpoint takes its document in
 * the request body, so a read travels by POST there exactly as a write would.
 * The document is parsed first and refused if it contains a mutation or a
 * subscription.
 *
 * GraphQL's id is an opaque global node ID and REST's id is a numeric database
 * ID, and each response calls its own one "id". REST node_id equals GraphQL
 * id; REST id equals GraphQL databaseId. A store that takes whichever field
 * arrived ends up with two key spaces for one entity.
 *
 * Environment:
 *   GITHUB_TOKEN   a token with read access to the repository
 *   GITHUB_REPO    owner/name
 *   GITHUB_ISSUE   an issue NUMBER, which is not its database id
 *   GITHUB_IDS     comma-separated identifiers from your own store
 */
const API = 'https://api.github.com';
const UA = 'github-graphql-id-crosswalk/1.0';

/** One repository plus one issue in a single document costs one point. */
export const POINTS_PER_QUERY = 1;

const LEGACY_DECODED = /^(\d+):([A-Za-z]+)(\d+)$/;
const NEW_NODE_ID = /^[A-Za-z]{1,4}_[A-Za-z0-9_-]{8,}$/;
const ALL_DIGITS = /^\d+$/;

const ISSUE_QUERY = 'query($owner: String!, $name: String!, $number: Int!) {'
  + ' repository(owner: $owner, name: $name) {'
  + ' id databaseId'
  + ' issue(number: $number) { id databaseId number } } }';

/** Remove GraphQL comments and string literals from a document. Pure. */
export function stripNoise(document) {
  const src = String(document ?? '');
  const out = [];
  let i = 0;
  while (i < src.length) {
    const ch = src[i];
    if (ch === '#') {
      while (i < src.length && src[i] !== '\n') i += 1;
      continue;
    }
    if (src.startsWith('"""', i)) {
      const j = src.indexOf('"""', i + 3);
      i = j < 0 ? src.length : j + 3;
      out.push(' ');
      continue;
    }
    if (ch === '"') {
      i += 1;
      while (i < src.length && src[i] !== '"') i += src[i] === '\\' ? 2 : 1;
      i += 1;
      out.push(' ');
      continue;
    }
    out.push(ch);
    i += 1;
  }
  return out.join('');
}

/** The top-level operations in a document, in order. Pure. */
export function operations(document) {
  const src = `${stripNoise(document)} `;
  const ops = [];
  let depth = 0;
  let word = '';
  let declared = null;
  for (const ch of src) {
    if (/[A-Za-z0-9_]/.test(ch)) { word += ch; continue; }
    if (word) {
      if (depth === 0 && ['query', 'mutation', 'subscription', 'fragment'].includes(word)) {
        declared = word;
      }
      word = '';
    }
    if (ch === '{') {
      if (depth === 0) { ops.push(declared || 'query'); declared = null; }
      depth += 1;
    } else if (ch === '}') {
      depth = Math.max(0, depth - 1);
    }
  }
  return ops;
}

/** Why this document will not be sent, or null if it is a read. Pure. */
export function refusal(document) {
  const ops = operations(document);
  if (ops.length === 0) return 'the document contains no operation to send.';
  for (const kind of ['mutation', 'subscription']) {
    if (ops.includes(kind)) {
      return `the document contains a ${kind}. This script sends queries only: `
        + 'a query is a read, and the section it belongs to promises its '
        + 'scripts never write.';
    }
  }
  return null;
}

/** [type, databaseId] for a legacy node ID, or null. Pure. */
export function decodeLegacyNodeId(value) {
  const text = String(value ?? '');
  if (!text || ALL_DIGITS.test(text)) return null;
  const padded = text + '='.repeat((4 - (text.length % 4)) % 4);
  let raw;
  try {
    raw = Buffer.from(padded, 'base64').toString('utf8');
    if (Buffer.from(raw, 'utf8').toString('base64').replace(/=+$/, '')
        !== padded.replace(/=+$/, '')) return null;
  } catch { return null; }
  const m = LEGACY_DECODED.exec(raw);
  if (!m) return null;
  const [, declaredLen, typeName, databaseId] = m;
  if (Number(declaredLen) !== typeName.length) return null;
  return [typeName, Number(databaseId)];
}

/** Which key space a stored identifier belongs to. Pure. */
export function idSpace(value) {
  if (typeof value === 'boolean') return 'unknown';
  if (typeof value === 'number' && Number.isInteger(value)) return 'rest-database-id';
  const text = String(value ?? '').trim();
  if (!text) return 'unknown';
  if (ALL_DIGITS.test(text)) return 'rest-database-id';
  if (decodeLegacyNodeId(text) || NEW_NODE_ID.test(text)) return 'graphql-node-id';
  return 'unknown';
}

/** The numeric key for this identifier without a network call, or null. Pure. */
export function toDatabaseId(value) {
  const space = idSpace(value);
  if (space === 'rest-database-id') return Number(String(value).trim());
  const decoded = decodeLegacyNodeId(value);
  return decoded ? decoded[1] : null;
}

/** Compare one object fetched both ways. Pure. */
export function crosswalk(restObject, graphqlObject) {
  const rest = (restObject && typeof restObject === 'object') ? restObject : {};
  const gql = (graphqlObject && typeof graphqlObject === 'object') ? graphqlObject : {};
  const restId = rest.id ?? null;
  const restNodeId = rest.node_id ?? null;
  const gqlId = gql.id ?? null;
  const gqlDatabaseId = gql.databaseId ?? null;
  return {
    rest_id: restId,
    rest_node_id: restNodeId,
    rest_number: rest.number ?? null,
    graphql_id: gqlId,
    graphql_database_id: gqlDatabaseId,
    graphql_number: gql.number ?? null,
    node_ids_match: Boolean(restNodeId) && restNodeId === gqlId,
    database_ids_match: restId !== null && restId === gqlDatabaseId,
    database_id_present: gqlDatabaseId !== null,
  };
}

/** Whether an object's number and database id differ. Pure. */
export function numberIsNotTheDatabaseId(restObject) {
  const rest = (restObject && typeof restObject === 'object') ? restObject : {};
  const number = rest.number ?? null;
  const databaseId = rest.id ?? null;
  if (number === null || databaseId === null) return null;
  return number !== databaseId;
}

/** Judge one crosswalk. Pure. Returns [state, detail]. */
export function classifyPair(restObject, graphqlObject) {
  const facts = crosswalk(restObject, graphqlObject);
  if (facts.rest_id === null || facts.graphql_id === null) {
    return ['incomplete', 'one of the two responses did not carry an '
      + 'identifier, so nothing can be compared.'];
  }
  if (!facts.database_id_present) {
    return ['database-id-absent', 'this type exposes no databaseId, so the node '
      + 'ID is the only key it has. A store that requires an integer has no row '
      + 'to write for it.'];
  }
  if (facts.node_ids_match && facts.database_ids_match) {
    return ['crosswalk-confirmed', 'REST node_id equals GraphQL id, and REST id '
      + 'equals GraphQL databaseId.'];
  }
  return ['crosswalk-broken', 'the two responses disagree, which means they are '
    + 'not the same object. Check that the number and the query are pointing at '
    + 'one thing before reading anything into the ids.'];
}

/** Judge a sample of stored identifiers. Pure. Returns [state, detail]. */
export function classifyStore(values) {
  const list = Array.isArray(values) ? values : [];
  if (list.length === 0) return ['no-sample', 'no identifiers were supplied to classify.'];
  const counts = { 'rest-database-id': 0, 'graphql-node-id': 0, unknown: 0 };
  for (const v of list) counts[idSpace(v)] += 1;
  if (counts['rest-database-id'] && counts['graphql-node-id']) {
    return ['mixed-key-space', 'one entity type is keyed two ways in the same '
      + `column: ${counts['rest-database-id']} database id(s) and `
      + `${counts['graphql-node-id']} node id(s).`];
  }
  if (counts.unknown === list.length) {
    return ['unrecognised', 'none of these look like either key space. They may '
      + 'be your own surrogate keys, which is fine and not this note.'];
  }
  if (counts['graphql-node-id']) {
    return ['consistent-node-id', 'every identifier is a global node ID. Read '
      + 'node_id from REST responses to keep it that way.'];
  }
  return ['consistent-database-id', 'every identifier is a numeric database ID. '
    + 'Request databaseId explicitly in every GraphQL selection to keep it that way.'];
}

/** How many identifiers appear in both lists, compared as given. Pure. */
export function joinRows(left, right) {
  const a = new Set((left || []).map((v) => String(v)));
  const b = new Set((right || []).map((v) => String(v)));
  let n = 0;
  for (const v of a) if (b.has(v)) n += 1;
  return n;
}

/** The same join after both sides are reduced to database ids. Pure. */
export function joinRowsNormalised(left, right) {
  const keys = (values) => {
    const out = new Set();
    for (const v of values || []) {
      const k = toDatabaseId(v);
      if (k !== null) out.add(k);
    }
    return out;
  };
  const a = keys(left);
  const b = keys(right);
  let n = 0;
  for (const v of a) if (b.has(v)) n += 1;
  return n;
}

/** How many stored ids can be rewritten offline, and how many cannot. Pure. */
export function migrationSplit(values) {
  let already = 0;
  let offline = 0;
  let refetch = 0;
  for (const v of values || []) {
    const space = idSpace(v);
    if (space === 'rest-database-id') already += 1;
    else if (space === 'graphql-node-id') {
      if (toDatabaseId(v) === null) refetch += 1; else offline += 1;
    }
  }
  return { already_numeric: already, decodable_offline: offline, needs_refetching: refetch };
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (state === 'mixed-key-space') {
    return 'pick one key space, request databaseId in every GraphQL selection '
      + 'or node_id from every REST response, and migrate the rows you hold. '
      + 'Do not join across the two.';
  }
  if (state === 'crosswalk-broken') {
    return 'stop and confirm both calls address the same object. An issue\'s '
      + 'number is not its databaseId, and using one where the other belongs '
      + 'is the usual cause.';
  }
  if (state === 'database-id-absent') {
    return 'key this entity by its node ID. There is no integer to store and '
      + 'decoding the node ID will not produce one.';
  }
  if (state === 'consistent-node-id') {
    return 'nothing to migrate. Keep reading node_id on the REST side so a new '
      + 'code path cannot introduce the other space.';
  }
  if (state === 'consistent-database-id') {
    return 'nothing to migrate. Keep asking for databaseId on the GraphQL side '
      + 'so a new code path cannot introduce the other space.';
  }
  if (state === 'unrecognised') {
    return 'nothing here is a GitHub identifier. Point the sample at the column '
      + 'that holds them.';
  }
  return 'fetch one object down both paths and compare the four fields before '
    + 'changing any schema.';
}

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'Content-Type': 'application/json',
    'User-Agent': UA,
  };
}

async function runQuery(token, document, variables) {
  const res = await fetch(`${API}/graphql`, {
    // A GraphQL query is a read. POST is only how the document reaches the
    // endpoint, and refusal() has already rejected anything that is not a read.
    method: 'POST',
    headers: headers(token),
    body: JSON.stringify({ query: document, variables: variables || {} }),
  });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return { status: res.status, body };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const repo = (process.env.GITHUB_REPO || "dummy-github-repo");
  const number = Number((process.env.GITHUB_ISSU || "dummy-github-issu")E || 0);
  if (!token || !repo || !number) {
    console.error('set GITHUB_TOKEN (read-only is enough), GITHUB_REPO=owner/name '
      + 'and GITHUB_ISSUE=<issue number>');
    process.exitCode = 2;
    return;
  }
  const [owner, name] = repo.split('/');
  const whyNot = refusal(ISSUE_QUERY);
  if (whyNot) {
    console.error(`refusing to send: ${whyNot}`);
    process.exitCode = 2;
    return;
  }
  console.log(`point cost: ${POINTS_PER_QUERY} point(s) against the 5,000/hour `
    + 'GraphQL budget, plus 1 core request');

  const restRes = await fetch(`${API}/repos/${owner}/${name}/issues/${number}`,
    { headers: headers(token) });
  const restObject = restRes.ok ? await restRes.json() : {};

  const { body } = await runQuery(token, ISSUE_QUERY, { owner, name, number });
  const repository = ((body || {}).data || {}).repository || {};
  const graphqlObject = repository.issue || {};

  console.log(`rest:    id=${restObject.id} node_id=${restObject.node_id} `
    + `number=${restObject.number}`);
  console.log(`graphql: databaseId=${graphqlObject.databaseId} `
    + `id=${graphqlObject.id} number=${graphqlObject.number}`);

  const [state, detail] = classifyPair(restObject, graphqlObject);
  console.log(`${state}: ${detail}`);
  console.log(`repair: ${repair(state)}`);

  const sample = String((process.env.GITHUB_ID || "dummy-github-id")S || '').split(',')
    .map((v) => v.trim()).filter(Boolean);
  const [storeState, storeDetail] = classifyStore(sample);
  const split = migrationSplit(sample);
  if (sample.length) {
    console.log(sample.map((v) => `${v} -> ${idSpace(v)}`).join(', '));
    console.log(`${storeState}: ${storeDetail}`);
    console.log(`migratable offline: ${split.decodable_offline}    needs `
      + `re-fetching: ${split.needs_refetching}    already numeric: `
      + `${split.already_numeric}`);
    console.log(`repair: ${repair(storeState)}`);
  }

  console.log(JSON.stringify({
    points_spent: POINTS_PER_QUERY,
    crosswalk: crosswalk(restObject, graphqlObject),
    state,
    store_state: storeState,
    migration: split,
  }, null, 2));
  process.exitCode = (state === 'crosswalk-broken' || storeState === 'mixed-key-space') ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
