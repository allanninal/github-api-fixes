/**
 * Show that a GraphQL 200 can carry an errors array a status check walks past.
 *
 * Read only, and queries only. GitHub's GraphQL endpoint takes a document in
 * the request body, so a read is carried by POST there just as a write would
 * be; that is a transport detail, not a licence to write. Any document
 * containing a mutation or a subscription is refused before a socket opens.
 *
 * Environment:
 *   GITHUB_TOKEN   a token with read access to the GraphQL API
 *   GITHUB_REPO    owner/name to probe
 *   GITHUB_QUERY   send your own query document instead of the default
 */
const API = 'https://api.github.com';
const UA = 'github-graphql-envelope/1.0';

/** A simple query costs one point. */
export const POINTS_PER_QUERY = 1;

const DEFAULT_QUERY = 'query($owner: String!, $name: String!) {'
  + ' repository(owner: $owner, name: $name) { name isPrivate } }';

/** The behaviours the documented error types actually demand. */
export const BEHAVIOUR = {
  RATE_LIMITED: ['wait', 'the point budget is spent. Wait for the reset that '
    + 'GET /rate_limit reports and do not retry before it.'],
  FORBIDDEN: ['alert', 'the token cannot see this. Retrying changes nothing; a '
    + 'human has to widen the permission or accept the gap.'],
  NOT_FOUND: ['record-absent', 'the resource is missing or invisible to this '
    + 'token. Record the absence; do not treat it as zero.'],
  MAX_NODE_LIMIT_EXCEEDED: ['reshape', 'the query asks for too many nodes and '
    + 'will fail identically every time. Lower the first values and paginate.'],
  INTERNAL: ['retry-once', "a failure on GitHub's side. Retry once with "
    + 'backoff, then give up and log the query.'],
  SERVICE_UNAVAILABLE: ['retry-once', "a transient failure on GitHub's side. "
    + 'Retry once with backoff.'],
};

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

/** The predicate a status-code client uses. Pure. */
export function statusSaysOk(status) {
  const n = Number(status);
  return Number.isFinite(n) && n >= 200 && n < 300;
}

/** The predicate a correct client uses. Pure. */
export function envelopeSaysOk(body) {
  if (!body || typeof body !== 'object') return false;
  return !(Array.isArray(body.errors) && body.errors.length > 0);
}

/** The type of every entry in the errors array, in order. Pure. */
export function errorTypes(body) {
  if (!body || typeof body !== 'object' || !Array.isArray(body.errors)) return [];
  return body.errors.map((e) => (e && typeof e === 'object' && e.type) || 'UNTYPED');
}

/** Whether any field in data resolved to something other than null. Pure. */
export function hasUsableData(body) {
  if (!body || typeof body !== 'object') return false;
  const data = body.data;
  if (!data || typeof data !== 'object' || Array.isArray(data)) return false;
  return Object.values(data).some((v) => v !== null && v !== undefined);
}

/** Whether a status check would pass on a response the envelope fails. Pure. */
export function predicatesDisagree(status, body) {
  return statusSaysOk(status) && !envelopeSaysOk(body);
}

/** What one error type demands of a client. Pure. Returns [action, detail]. */
export function behaviourFor(errorType) {
  if (Object.prototype.hasOwnProperty.call(BEHAVIOUR, errorType)) {
    return BEHAVIOUR[errorType];
  }
  return ['log-verbatim', 'an error type this script does not know. Log it '
    + 'verbatim and fail the call rather than guessing; new types get added '
    + 'over time.'];
}

/** Classify one response envelope. Pure. Returns [state, detail]. */
export function classify(status, body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return ['unreadable', 'the response was not a JSON object, so neither '
      + 'predicate can be evaluated over it.'];
  }
  if (!statusSaysOk(status)) {
    return ['transport-failure', `HTTP ${status}, which a status check already `
      + 'catches. The errors array is not where this one hides.'];
  }
  const types = errorTypes(body);
  if (types.length === 0) {
    return ['200-clean', 'the status line and the errors array agree that this '
      + 'worked. Both predicates pass, which on this response is agreement '
      + 'rather than proof that your client checks the second one.'];
  }
  const named = [...new Set(types)].sort().join(', ');
  if (hasUsableData(body)) {
    return ['200-with-errors-and-data',
      `${types.length} error(s) of type ${named} arrived with usable data, `
      + 'which is partial success and a different repair.'];
  }
  return ['200-with-errors-no-data',
    `the status line says success and the body carries ${types.length} error(s) `
    + `of type ${named} with no usable data.`];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (state === '200-with-errors-no-data') {
    return 'read body.errors before body.data and branch on errors[].type. Put '
      + 'the check in the function that sends queries so no caller can skip it.';
  }
  if (state === '200-with-errors-and-data') {
    return 'see /github/graphql-partial-data-nulls/ -- do not retry this one. '
      + 'Some fields resolved and discarding them because the call carried '
      + 'errors loses data that arrived correctly.';
  }
  if (state === 'transport-failure') {
    return 'handle the status code as you already do. This note is about the '
      + 'failures that arrive as a 200.';
  }
  if (state === '200-clean') {
    return 'nothing on this response. Check that the errors array is read at '
      + 'all: the two predicates agree here and part company on the first '
      + 'failure.';
  }
  return 'point the check at a document this endpoint can answer.';
}

/** Points this run will spend against the GraphQL budget. Pure. */
export function pointCost(probes) {
  return (Array.isArray(probes) ? probes.length : 0) * POINTS_PER_QUERY;
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
  if (!token || !repo) {
    console.error('set GITHUB_TOKEN (read-only is enough) and GITHUB_REPO=owner/name');
    process.exitCode = 2;
    return;
  }
  const [owner, name] = repo.split('/');
  if (!owner || !name) {
    console.error('GITHUB_REPO takes owner/name');
    process.exitCode = 2;
    return;
  }
  const document = (process.env.GITHUB_QUER || "dummy-github-quer")Y || DEFAULT_QUERY;
  const whyNot = refusal(document);
  if (whyNot) {
    console.error(`refusing to send: ${whyNot}`);
    process.exitCode = 2;
    return;
  }

  const probes = [
    ['missing-repository', { owner, name: `${name}-does-not-exist-probe` }],
    ['as-configured', { owner, name }],
  ];
  console.log(`point cost: ${pointCost(probes)} point(s) against the 5,000/hour GraphQL budget`);

  const findings = [];
  for (const [label, variables] of probes) {
    const { status, body } = await runQuery(token, document, variables);
    const [state, detail] = classify(status, body);
    const types = errorTypes(body);
    console.log(`probe ${label}: HTTP ${status}, errors=${types.length}, `
      + `data present=${hasUsableData(body) ? 'yes' : 'no'}`);
    console.log(`${state}: ${detail}`);
    console.log(`status check passes: ${statusSaysOk(status) ? 'yes' : 'no'}    `
      + `envelope check passes: ${envelopeSaysOk(body) ? 'yes' : 'no'}    `
      + `they disagree: ${predicatesDisagree(status, body) ? 'yes' : 'no'}`);
    for (const t of [...new Set(types)].sort()) {
      const [action, why] = behaviourFor(t);
      console.log(`  ${t} -> ${action}: ${why}`);
    }
    console.log(`repair: ${repair(state)}`);

    findings.push({
      probe: label,
      status,
      error_types: types,
      has_usable_data: hasUsableData(body),
      status_check_passes: statusSaysOk(status),
      envelope_check_passes: envelopeSaysOk(body),
      predicates_disagree: predicatesDisagree(status, body),
      state,
      detail,
    });
  }

  console.log(JSON.stringify({ points_spent: pointCost(probes), findings }, null, 2));
  process.exitCode = findings.some((f) => f.predicates_disagree) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
