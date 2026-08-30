/**
 * Find nested GraphQL connections that truncated once per parent.
 *
 * Read only, and queries only. GitHub's GraphQL endpoint takes a document in
 * the request body, so a read is carried by POST there just as a write would
 * be; that is transport, not intent. Any document containing a mutation or a
 * subscription is refused before a socket opens.
 *
 * Environment:
 *   GITHUB_TOKEN      a token with read access to the GraphQL API
 *   GITHUB_LOGIN      user or organisation to probe with the default query
 *   GITHUB_OUTER      outer page size (default 5)
 *   GITHUB_INNER      inner page size (default 5)
 *   GITHUB_QUERY      the document as a string
 *   GITHUB_VARIABLES  JSON object of variables
 */
const API = 'https://api.github.com';
const UA = 'github-graphql-nested/1.0';

export const POINTS_PER_QUERY = 1;

const DEFAULT_QUERY = 'query($login: String!, $outer: Int = 5, $inner: Int = 5) {'
  + ' repositoryOwner(login: $login) {'
  + ' repositories(first: $outer, orderBy: {field: PUSHED_AT, direction: DESC}) {'
  + ' totalCount pageInfo { hasNextPage endCursor }'
  + ' nodes { name issues(first: $inner, states: OPEN) {'
  + ' totalCount nodes { number } } }'
  + ' } } }';

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

/** A selection set with everything nested inside it blanked out. Pure. */
export function outerText(block) {
  const out = [];
  let depth = 0;
  for (const ch of String(block ?? '')) {
    if (ch === '{') { depth += 1; out.push(' '); continue; }
    if (ch === '}') { depth = Math.max(0, depth - 1); out.push(' '); continue; }
    out.push(depth === 0 ? ch : ' ');
  }
  return out.join('');
}

/** Every connection in the document, found by shape. Pure. */
export function connectionFields(document, depthIn = 0, stripped = false) {
  const src = stripped ? String(document ?? '') : stripNoise(document);
  const out = [];
  let i = 0;
  let word = '';
  let field = '';
  while (i < src.length) {
    const ch = src[i];
    if (/[A-Za-z0-9_]/.test(ch)) { word += ch; i += 1; continue; }
    // The field name has to survive the whitespace and the argument list
    // between it and its selection set, so it is remembered rather than read
    // off whatever happens to precede the brace.
    if (word) { field = word; word = ''; }
    if (ch === '(') {
      let j = i;
      let level = 0;
      while (j < src.length) {
        if (src[j] === '(') level += 1;
        else if (src[j] === ')') { level -= 1; if (level === 0) break; }
        j += 1;
      }
      i = j + 1;
      continue;
    }
    if (ch === '{') {
      let j = i;
      let level = 0;
      while (j < src.length) {
        if (src[j] === '{') level += 1;
        else if (src[j] === '}') { level -= 1; if (level === 0) break; }
        j += 1;
      }
      const block = src.slice(i + 1, j);
      const own = outerText(block).split(/\s+/).filter(Boolean);
      if (field && (own.includes('nodes') || own.includes('edges'))) {
        out.push({
          field,
          depth: depthIn,
          has_page_info: own.includes('pageInfo'),
          has_total_count: own.includes('totalCount'),
        });
        out.push(...connectionFields(block, depthIn + 1, true));
      } else {
        out.push(...connectionFields(block, depthIn, true));
      }
      i = j + 1;
      field = '';
      continue;
    }
    if (ch === '}') field = '';
    i += 1;
  }
  return out;
}

/** Inner connections that asked for neither totalCount nor pageInfo. Pure. */
export function unauditable(fields) {
  return (fields || []).filter((f) => f.depth >= 1 && !f.has_total_count && !f.has_page_info);
}

/** Inner connections that can be seen to truncate but not continued. Pure. */
export function unresumable(fields) {
  return (fields || []).filter((f) => f.depth >= 1 && f.has_total_count && !f.has_page_info);
}

/** Whether a decoded object is a connection. Pure. */
export function isConnection(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  return Array.isArray(value.nodes) || Array.isArray(value.edges);
}

/** Every connection in a decoded response, with its path and depth. Pure. */
export function walkConnections(data, path = '', depth = 0) {
  const out = [];
  if (Array.isArray(data)) {
    data.forEach((item, index) => {
      out.push(...walkConnections(item, `${path}[${index}]`, depth));
    });
    return out;
  }
  if (!data || typeof data !== 'object') return out;
  let next = depth;
  if (isConnection(data)) {
    const items = Array.isArray(data.nodes) ? data.nodes : (data.edges || []);
    const page = (data.pageInfo && typeof data.pageInfo === 'object') ? data.pageInfo : null;
    out.push({
      path: path || '(root)',
      depth,
      returned: items.length,
      total_count: Number.isInteger(data.totalCount) ? data.totalCount : null,
      has_next_page: page ? page.hasNextPage : null,
      end_cursor: page ? page.endCursor : null,
    });
    next = depth + 1;
  }
  for (const [key, value] of Object.entries(data)) {
    out.push(...walkConnections(value, path ? `${path}.${key}` : key, next));
  }
  return out;
}

/** Items this connection holds and did not return, or null. Pure. */
export function missing(entry) {
  const total = entry && entry.total_count;
  if (!Number.isInteger(total)) return null;
  return Math.max(0, total - ((entry && entry.returned) || 0));
}

/** Whether this connection stopped short of what it holds. Pure. */
export function truncated(entry) {
  if (entry && entry.has_next_page === true) return true;
  return !!missing(entry);
}

/** Whether the response says anything at all about completeness. Pure. */
export function auditable(entry) {
  if (!entry) return false;
  return entry.total_count !== null || (entry.has_next_page !== null
    && entry.has_next_page !== undefined);
}

/** Whether this connection can be continued without refetching its parent. */
export function resumable(entry) {
  if (!entry) return false;
  return !!entry.end_cursor || (entry.has_next_page !== null
    && entry.has_next_page !== undefined);
}

/** Queries a correct inner walk would cost, from what this response shows. */
export function followupQueries(entries) {
  let total = 0;
  for (const entry of entries || []) {
    if ((entry.depth || 0) < 1 || !truncated(entry)) continue;
    const gap = missing(entry);
    const page = entry.returned || 0;
    if (gap && page > 0) total += Math.ceil(gap / page);
    else total += 1;
  }
  return total;
}

/** Classify one response. Pure. Returns [state, detail]. */
export function classify(entries) {
  if (!entries || entries.length === 0) {
    return ['no-connection-in-the-response', 'nothing in this response has '
      + 'nodes or edges, so there is no connection here to be truncated.'];
  }
  const inner = entries.filter((e) => e.depth >= 1);
  const innerCut = inner.filter((e) => truncated(e));
  if (innerCut.length) {
    const gaps = innerCut.map((e) => missing(e)).filter((g) => g !== null);
    const sum = gaps.length ? gaps.reduce((a, b) => a + b, 0) : 'an unknown number of';
    return ['inner-connection-truncated',
      `${innerCut.length} of ${inner.length} inner connection(s) returned fewer `
      + `items than they contain and ${sum} item(s) are missing with no error raised.`];
  }
  const blind = inner.filter((e) => !auditable(e));
  if (blind.length) {
    return ['inner-connection-unauditable',
      `${blind.length} of ${inner.length} inner connection(s) asked for neither `
      + 'totalCount nor pageInfo, so this response cannot say whether they truncated.'];
  }
  const outerCut = entries.filter((e) => e.depth === 0 && truncated(e));
  if (outerCut.length) {
    return ['outer-connection-truncated', 'the outer connection has more pages '
      + 'and every inner connection in it is complete. This is the truncation '
      + 'people do notice.'];
  }
  return ['complete', 'every connection in this response returned everything it '
    + 'holds, so a total computed over it really is a total.'];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (state === 'inner-connection-truncated') {
    return 'add pageInfo { hasNextPage endCursor } to every nested connection '
      + 'and walk each truncated parent separately with after: endCursor. An '
      + 'outer loop cannot do this for you.';
  }
  if (state === 'inner-connection-unauditable') {
    return 'add totalCount and pageInfo { hasNextPage endCursor } to the nested '
      + 'connections first. They cost nothing and without them nobody can tell '
      + 'whether this response is complete.';
  }
  if (state === 'outer-connection-truncated') {
    return 'follow the outer cursor as you already do, and keep checking the '
      + 'inner connections on every page: they restart from the beginning each '
      + 'time the outer one advances.';
  }
  if (state === 'complete') {
    return 'nothing here. Re-run it against a parent that really has more than '
      + 'one page of children, since a connection that fits cannot demonstrate '
      + 'a connection that does not.';
  }
  return 'point the query at something with a connection in it.';
}

/** Points this run will spend. Pure. */
export function pointCost(queries) {
  return Number(queries || 0) * POINTS_PER_QUERY;
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
  if (!token) {
    console.error('set GITHUB_TOKEN (a read-only token is enough)');
    process.exitCode = 2;
    return;
  }
  const document = (process.env.GITHUB_QUER || "dummy-github-quer")Y || DEFAULT_QUERY;
  let variables = {};
  try { variables = JSON.parse((process.env.GITHUB_VARIABLE || "dummy-github-variable")S || '{}'); } catch {
    console.error('GITHUB_VARIABLES takes a JSON object');
    process.exitCode = 2;
    return;
  }
  if (!(process.env.GITHUB_QUERY || "dummy-github-query")) {
    if (!(process.env.GITHUB_LOGIN || "dummy-github-login")) {
      console.error('set GITHUB_LOGIN to a user or organisation name');
      process.exitCode = 2;
      return;
    }
    variables.login = (process.env.GITHUB_LOGIN || "dummy-github-login");
    variables.outer = Number((process.env.GITHUB_OUTE || "dummy-github-oute")R || 5);
    variables.inner = Number((process.env.GITHUB_INNE || "dummy-github-inne")R || 5);
  }

  const whyNot = refusal(document);
  if (whyNot) {
    console.error(`refusing to send: ${whyNot}`);
    process.exitCode = 2;
    return;
  }

  console.log(`point cost: ${pointCost(1)} point(s) against the 5,000/hour GraphQL budget`);
  const fields = connectionFields(document);
  for (const f of unauditable(fields)) {
    console.log(`document: ${f.field} asks for neither totalCount nor pageInfo, `
      + 'so no response can say whether it truncated');
  }
  for (const f of unresumable(fields)) {
    console.log(`document: ${f.field} asks for totalCount but not pageInfo, so `
      + 'truncation is visible and cannot be resumed without refetching the parent');
  }

  const { status, body } = await runQuery(token, document, variables);
  if (!body || typeof body !== 'object') {
    console.error(`HTTP ${status} and no JSON body to read`);
    process.exitCode = 2;
    return;
  }
  if (Array.isArray(body.errors) && body.errors.length) {
    console.error(`the query itself failed: ${JSON.stringify(body.errors).slice(0, 400)}`);
    process.exitCode = 2;
    return;
  }

  const entries = walkConnections(body.data || {});
  for (const e of entries) {
    const gap = missing(e);
    let note = 'complete';
    if (truncated(e)) {
      note = gap !== null ? `${gap} missing` : 'more pages';
      if (!resumable(e)) note += ', no cursor';
    }
    console.log(`  ${e.path}  depth ${e.depth}  ${e.returned} of `
      + `${e.total_count === null ? '?' : e.total_count}  ${note}`);
  }

  const [state, detail] = classify(entries);
  console.log(`${state}: ${detail}`);
  const follow = followupQueries(entries);
  if (follow) {
    console.log(`following them properly costs ${follow} more `
      + `${follow === 1 ? 'query' : 'queries'}, at least one per truncated parent`);
  }
  console.log(`repair: ${repair(state)}`);

  console.log(JSON.stringify({
    points_spent: pointCost(1), state, followup_queries: follow,
    connections: entries, document: fields,
  }, null, 2));
  process.exitCode = ['inner-connection-truncated',
    'inner-connection-unauditable'].includes(state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
