/**
 * Compute a GraphQL query's node count from its text, before sending it.
 *
 * Read only, and by default not even that: the node count is derived from the
 * query document, so the standard run makes no request, needs no token and
 * spends no points. The optional confirm step sends the document once and
 * costs one point.
 *
 * Queries only. GitHub's GraphQL endpoint takes a document in the request body,
 * so a read is carried by POST there just as a write would be; that is
 * transport, not intent. Any document containing a mutation or a subscription
 * is refused before a socket opens.
 *
 * Environment:
 *   GITHUB_QUERY      the query document itself
 *   GITHUB_VARIABLES  JSON object supplying the query's variables
 *   GITHUB_CONFIRM    set to spend one point asking the server to agree
 *   GITHUB_TOKEN      only needed when confirming
 */
const API = 'https://api.github.com';
const UA = 'github-graphql-nodes/1.0';

/** The documented ceiling on one query. */
export const NODE_LIMIT = 500000;

/** Above this fraction of the cap, say so. */
export const NEAR = 0.8;

const DEMO_QUERY = `query {
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

const PAGING = /\b(first|last)\s*:\s*(\$?[A-Za-z0-9_]+)/;
const SPREAD = /\.\.\.\s*([A-Za-z_][A-Za-z0-9_]*)/g;

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

/** Group a count in thousands so it can be read at a glance. Pure. */
export function commas(n) {
  if (n === null || n === undefined || n === '') return String(n);
  const v = Number(n);
  if (!Number.isFinite(v)) return String(n);
  return Math.trunc(v).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

function paging(field, args, variables) {
  const m = PAGING.exec(args || '');
  if (!m) return null;
  const [, arg, raw] = m;
  const variable = raw.startsWith('$') ? raw.slice(1) : null;
  const value = variable ? (variables || {})[variable] : raw;
  const n = Number(value);
  return {
    field: field || '?',
    arg,
    variable,
    requested: Number.isInteger(n) ? n : null,
  };
}

/** Every sliced connection in a document, with its node contribution. Pure. */
export function connections(document, variables = null) {
  const src = stripNoise(document);
  const out = [];
  const stack = [];
  let pending = null;
  let field = null;
  let i = 0;
  while (i < src.length) {
    const ch = src[i];
    if (ch === '(') {
      const j = src.indexOf(')', i);
      const args = j < 0 ? src.slice(i + 1) : src.slice(i + 1, j);
      const found = paging(field, args, variables);
      // Only overwrite a pending slice when this argument group carries one, so
      // a directive such as @include(if: $x) cannot erase the first: value that
      // came immediately before it.
      if (found !== null) pending = found;
      i = j < 0 ? src.length : j + 1;
      continue;
    }
    if (ch === '{') {
      if (pending !== null) {
        const ancestors = stack.reduce((a, b) => a * b, 1);
        const rec = { ...pending, depth: stack.length + 1, ancestors };
        rec.nodes = rec.requested === null ? null : ancestors * rec.requested;
        out.push(rec);
        stack.push(rec.requested || 1);
      } else {
        stack.push(1);
      }
      pending = null;
      field = null;
      i += 1;
      continue;
    }
    if (ch === '}') {
      stack.pop();
      pending = null;
      field = null;
      i += 1;
      continue;
    }
    if (/[A-Za-z_]/.test(ch)) {
      let j = i;
      while (j < src.length && /[A-Za-z0-9_]/.test(src[j])) j += 1;
      field = src.slice(i, j);
      i = j;
      continue;
    }
    i += 1;
  }
  return out;
}

/** The node total the server will compute for this document. Pure. */
export function nodeCount(document, variables = null) {
  return connections(document, variables)
    .filter((c) => c.nodes !== null)
    .reduce((sum, c) => sum + c.nodes, 0);
}

/** Connections whose slice is a variable nobody supplied. Pure. */
export function unresolved(document, variables = null) {
  return connections(document, variables)
    .filter((c) => c.requested === null)
    .map((c) => c.field);
}

/** Named fragment spreads, which hide part of the selection set. Pure. */
export function fragmentSpreads(document) {
  const src = stripNoise(document);
  const found = new Set();
  for (const m of src.matchAll(SPREAD)) {
    if (m[1] !== 'on') found.add(m[1]);
  }
  return [...found].sort();
}

/** Everything that makes the computed total less than certain. Pure. */
export function caveats(document, variables = null) {
  const out = [];
  const missing = [...new Set(unresolved(document, variables))].sort();
  if (missing.length > 0) {
    out.push(`the slice on ${missing.join(', ')} is a variable this run has no `
      + 'value for, so those connections are not in the total. Pass GITHUB_VARIABLES.');
  }
  const spreads = fragmentSpreads(document);
  if (spreads.length > 0) {
    out.push(`the document spreads the fragment(s) ${spreads.join(', ')}, whose `
      + 'selection set this text-level check does not expand, so the total is a '
      + 'lower bound.');
  }
  return out;
}

/** The connection carrying the largest multiplier. Pure. null if there is none. */
export function deepest(document, variables = null) {
  const resolved = connections(document, variables).filter((c) => c.nodes !== null);
  if (resolved.length === 0) return null;
  return resolved.reduce((best, c) => {
    if (c.depth > best.depth) return c;
    if (c.depth === best.depth && c.nodes > best.nodes) return c;
    return best;
  }, resolved[0]);
}

/** The largest slice the deepest connection could take. Pure. */
export function reshape(document, variables = null, limit = NODE_LIMIT) {
  const d = deepest(document, variables);
  if (d === null) return [null, null, null];
  const total = nodeCount(document, variables);
  const room = limit - (total - d.nodes);
  if (d.ancestors <= 0) return [d.field, d.requested, null];
  const k = Math.floor(room / d.ancestors);
  if (k < 1) return [d.field, d.requested, null];
  return [d.field, d.requested, Math.min(k, 100)];
}

/** Whether this node total is over the cap. Pure. */
export function exceeds(count, limit = NODE_LIMIT) {
  const n = Number(count);
  return Number.isFinite(n) && n > Number(limit);
}

/** Classify one document. Pure. Returns [state, detail]. */
export function verdict(document, variables = null, limit = NODE_LIMIT) {
  const conns = connections(document, variables);
  if (conns.length === 0) {
    return ['no-connections', 'this document slices no connections, so it has '
      + 'no node count worth speaking of.'];
  }
  if (conns.some((c) => c.nodes === null)) {
    return ['unresolved-variables', 'at least one slice is a variable with no '
      + 'value supplied, so the node count cannot be computed from the text alone.'];
  }
  const total = nodeCount(document, variables);
  const pct = Math.round((100 * total) / limit);
  if (exceeds(total, limit)) {
    return ['over-node-limit', `${commas(total)} nodes is ${pct}% of the `
      + `${commas(limit)} cap, so this query is rejected before it runs whatever `
      + 'the organisation contains.'];
  }
  if (total > limit * NEAR) {
    return ['near-node-limit', `${commas(total)} nodes is ${pct}% of the cap, `
      + 'which leaves no room for another level.'];
  }
  return ['within-node-limit', `${commas(total)} nodes is ${pct}% of the cap.`];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state, field = null, current = null, suggested = null) {
  if (state === 'over-node-limit' || state === 'near-node-limit') {
    if (suggested !== null && suggested !== undefined) {
      return `lower first on ${field} from ${current} to ${suggested} and `
        + 'paginate it separately with pageInfo { hasNextPage endCursor }.';
    }
    return `even a slice of one on ${field} leaves this query over the cap, so `
      + 'split it into separate queries rather than tuning a number.';
  }
  if (state === 'unresolved-variables') {
    return 'pass the variables with GITHUB_VARIABLES so the slices can be '
      + 'resolved. A slice you cannot evaluate is a slice you cannot budget for.';
  }
  if (state === 'no-connections') return 'nothing. There is nothing here to multiply.';
  return 'nothing on the node count.';
}

/** The node count the server computed, if the document asked for it. Pure. */
export function reportedNodeCount(body) {
  if (!body || typeof body !== 'object') return null;
  const data = body.data;
  if (!data || typeof data !== 'object') return null;
  const rl = data.rateLimit;
  if (!rl || typeof rl !== 'object') return null;
  const n = Number(rl.nodeCount);
  return Number.isFinite(n) ? Math.trunc(n) : null;
}

/** Whether the server refused this document for its size. Pure. */
export function rejectedForNodes(body) {
  if (!body || typeof body !== 'object' || !Array.isArray(body.errors)) return false;
  return body.errors.some(
    (e) => e && typeof e === 'object' && e.type === 'MAX_NODE_LIMIT_EXCEEDED',
  );
}

/** Points this run will spend. Pure. Zero unless the confirm step is asked for. */
export function pointCost(confirm) {
  return confirm ? 1 : 0;
}

async function main() {
  const document = (process.env.GITHUB_QUER || "dummy-github-quer")Y || DEMO_QUERY;
  let variables = {};
  if ((process.env.GITHUB_VARIABLES || "dummy-github-variables")) {
    try { variables = JSON.parse((process.env.GITHUB_VARIABLES || "dummy-github-variables")); } catch {
      console.error('GITHUB_VARIABLES must be a JSON object');
      process.exitCode = 2;
      return;
    }
  }
  const confirm = Boolean((process.env.GITHUB_CONFIRM || "dummy-github-confirm"));

  const whyNot = refusal(document);
  if (whyNot) {
    console.error(`refusing to analyse and send: ${whyNot}`);
    process.exitCode = 2;
    return;
  }

  if (confirm) {
    console.log(`point cost: ${pointCost(true)} point(s) against the 5,000/hour GraphQL budget`);
  } else {
    console.log(`point cost: ${pointCost(false)} point(s). The node count is `
      + 'computed from the query text and nothing is sent.');
  }

  const conns = connections(document, variables);
  const total = nodeCount(document, variables);
  console.log(`node count: ${commas(total)} against a limit of ${commas(NODE_LIMIT)}`);
  for (const c of conns) {
    console.log(`  ${c.field.padEnd(16)} ${c.arg}=${String(c.requested ?? '?').padEnd(6)} `
      + `depth ${String(c.depth).padEnd(3)} ancestors x${commas(c.ancestors).padEnd(8)} `
      + `${c.nodes === null ? '?' : commas(c.nodes)} nodes`);
  }

  const [state, detail] = verdict(document, variables);
  console.log(`${state}: ${detail}`);
  for (const c of caveats(document, variables)) console.log(`caveat: ${c}`);
  const [field, current, suggested] = reshape(document, variables);
  console.log(`repair: ${repair(state, field, current, suggested)}`);

  let server = {};
  if (confirm) {
    const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
    if (!token) {
      console.error('confirming needs GITHUB_TOKEN (read-only is enough)');
      process.exitCode = 2;
      return;
    }
    const res = await fetch(`${API}/graphql`, {
      // A GraphQL query is a read. POST is only how the document reaches the
      // endpoint, and refusal() has already rejected anything that is not.
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github+json',
        'Content-Type': 'application/json',
        'User-Agent': UA,
      },
      body: JSON.stringify({ query: document, variables }),
    });
    let body = null;
    try { body = await res.json(); } catch { body = null; }
    server = { rejected: rejectedForNodes(body), reported_node_count: reportedNodeCount(body) };
    if (server.rejected) {
      console.log('the server rejected the document for its node count, which '
        + 'confirms the arithmetic above');
    } else if (server.reported_node_count !== null) {
      console.log(`the server computed ${commas(server.reported_node_count)} node(s); `
        + `this check computed ${commas(total)}`);
    } else {
      console.log('the server accepted the document and reported no node count. '
        + 'Add rateLimit { nodeCount } to compare directly.');
    }
  }

  console.log(JSON.stringify({
    points_spent: pointCost(confirm),
    node_count: total,
    node_limit: NODE_LIMIT,
    over_limit: exceeds(total),
    connections: conns,
    caveats: caveats(document, variables),
    deepest: deepest(document, variables),
    suggested: { field, current, first: suggested },
    server,
    state,
    detail,
  }, null, 2));
  process.exitCode = ['over-node-limit', 'near-node-limit'].includes(state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
