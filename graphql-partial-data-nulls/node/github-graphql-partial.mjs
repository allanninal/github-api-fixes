/**
 * Separate the fields a GraphQL response withheld from the ones that are empty.
 *
 * Read only, and queries only. GitHub's GraphQL endpoint takes a document in
 * the request body, so a read is carried by POST there just as a write would
 * be; that is transport, not intent. Any document containing a mutation or a
 * subscription is refused before a socket opens.
 *
 * Environment:
 *   GITHUB_TOKEN   a token with read access to the GraphQL API
 *   GITHUB_REPO    owner/name
 *   GITHUB_QUERY   send your own query document instead of the default
 *   GITHUB_ROOT    the path you aggregate over, default 'repository'
 */
const API = 'https://api.github.com';
const UA = 'github-graphql-partial/1.0';

export const POINTS_PER_QUERY = 1;

const DEFAULT_QUERY = 'query($owner: String!, $name: String!) {'
  + ' repository(owner: $owner, name: $name) {'
  + ' name isPrivate diskUsage'
  + ' licenseInfo { key }'
  + ' collaborators(first: 1) { totalCount }'
  + ' } }';

/** What a withheld field would need to be readable. */
export const PERMISSION_HINT = {
  diskUsage: 'metadata read plus admin on the repository',
  collaborators: 'read access to repository members',
  vulnerabilityAlerts: 'Dependabot alerts read',
  projectsV2: 'organization projects read',
  members: 'read:org on the organization',
  email: 'user email read, and the user must have a public email',
};

const MISSING = Symbol('missing');

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

/** A GraphQL error path rendered as a dotted string. Pure. */
export function pathKey(path) {
  if (typeof path === 'string') return path;
  return (path || []).map(String).join('.');
}

/** Resolve a dotted path in a data tree. Pure. MISSING if there is no such path. */
export function valueAt(data, dotted) {
  let cur = data;
  if (!dotted) return cur;
  for (const seg of String(dotted).split('.')) {
    if (Array.isArray(cur)) {
      const i = Number(seg);
      if (!Number.isInteger(i) || i < 0 || i >= cur.length) return MISSING;
      cur = cur[i];
    } else if (cur && typeof cur === 'object') {
      if (!Object.prototype.hasOwnProperty.call(cur, seg)) return MISSING;
      cur = cur[seg];
    } else {
      return MISSING;
    }
  }
  return cur;
}

/** Every path in a data tree whose value is null. Pure. */
export function nullPaths(data, prefix = '') {
  const out = [];
  let entries;
  if (Array.isArray(data)) entries = data.map((v, i) => [String(i), v]);
  else if (data && typeof data === 'object') entries = Object.entries(data);
  else return out;
  for (const [key, value] of entries) {
    const here = prefix ? `${prefix}.${key}` : key;
    if (value === null || value === undefined) out.push(here);
    else out.push(...nullPaths(value, here));
  }
  return out.sort();
}

/** Dotted paths named by the errors array, mapped to their type. Pure. */
export function errorPaths(body) {
  const out = {};
  if (!body || typeof body !== 'object' || !Array.isArray(body.errors)) return out;
  for (const err of body.errors) {
    if (!err || typeof err !== 'object' || !err.path || err.path.length === 0) continue;
    out[pathKey(err.path)] = err.type || 'UNTYPED';
  }
  return out;
}

/** Errors that name no field, so nothing can be attributed to them. Pure. */
export function unpathedErrors(body) {
  if (!body || typeof body !== 'object' || !Array.isArray(body.errors)) return 0;
  return body.errors.filter(
    (e) => !e || typeof e !== 'object' || !e.path || e.path.length === 0,
  ).length;
}

/** Whether any top-level field resolved to something other than null. Pure. */
export function hasUsableData(body) {
  if (!body || typeof body !== 'object') return false;
  const data = body.data;
  if (!data || typeof data !== 'object' || Array.isArray(data)) return false;
  return Object.values(data).some((v) => v !== null && v !== undefined);
}

/** Paths that are null in data and explained by an errors entry. Pure. */
export function withheld(body) {
  if (!body || typeof body !== 'object') return [];
  const named = errorPaths(body);
  const nulls = new Set(nullPaths(body.data));
  return Object.keys(named).filter((p) => nulls.has(p)).sort();
}

/** Paths that are null with no errors entry: genuinely empty, not hidden. Pure. */
export function absent(body) {
  if (!body || typeof body !== 'object') return [];
  const named = new Set(Object.keys(errorPaths(body)));
  return nullPaths(body.data).filter((p) => !named.has(p)).sort();
}

/** Error paths that do not resolve to a null in data. Pure. */
export function orphanErrorPaths(body) {
  if (!body || typeof body !== 'object') return [];
  const nulls = new Set(nullPaths(body.data));
  return Object.keys(errorPaths(body)).filter((p) => !nulls.has(p)).sort();
}

/** The permission a withheld field would want. Pure. */
export function permissionHint(dotted) {
  const leaf = String(dotted).split('.').pop();
  return Object.prototype.hasOwnProperty.call(PERMISSION_HINT, leaf)
    ? PERMISSION_HINT[leaf]
    : 'the permission that covers this field';
}

/** Counts for one response. Pure. */
export function tally(body) {
  return {
    withheld: withheld(body).length,
    absent: absent(body).length,
    orphaned: orphanErrorPaths(body).length,
    unpathed_errors: unpathedErrors(body),
  };
}

/** Data survived and errors arrived beside it. Pure. */
export function isPartialSuccess(body) {
  if (!body || typeof body !== 'object') return false;
  return Array.isArray(body.errors) && body.errors.length > 0 && hasUsableData(body);
}

/** Whether a sum under this root is honest. Pure. Returns [bool, sentence]. */
export function safeToAggregate(body, root) {
  const under = withheld(body).filter(
    (p) => !root || p === root || p.startsWith(`${root}.`),
  );
  if (under.length === 0) {
    return [true, `no withheld fields under '${root}', so a total over it is a total.`];
  }
  return [false, `${under.length} withheld field(s) under '${root}', so a total `
    + 'over it is a lower bound and has to be labelled as one.'];
}

/** Classify one response. Pure. Returns [state, detail]. */
export function classify(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return ['unreadable', 'the response was not a JSON object, so nothing can '
      + 'be counted in it.'];
  }
  const errs = Array.isArray(body.errors) ? body.errors : [];
  const hidden = withheld(body);
  const empty = absent(body);
  if (errs.length > 0 && !hasUsableData(body)) {
    return ['total-failure', `${errs.length} error(s) arrived and no field `
      + 'resolved, so this is a failed query wearing a 200 rather than a '
      + 'partial one.'];
  }
  if (errs.length > 0 && hidden.length === 0 && unpathedErrors(body) > 0) {
    return ['errors-without-path', `${unpathedErrors(body)} error(s) arrived `
      + 'beside usable data but none of them names a field, so nothing can be '
      + 'attributed to a column.'];
  }
  if (hidden.length > 0) {
    return ['partial-withheld', `${hidden.length} field(s) resolved to null and `
      + `errors[].path explains ${hidden.length === 2 ? 'both' : 'each of them'}.`];
  }
  if (empty.length > 0) {
    return ['nulls-unexplained', `${empty.length} null(s) in the data and no `
      + 'errors entry for any of them, so they are genuinely empty rather than '
      + 'withheld.'];
  }
  return ['complete', 'every requested field resolved and the errors array is empty.'];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (state === 'partial-withheld') {
    return 'record the withheld paths as unknown, not zero, and label the total '
      + 'a lower bound. Do not retry: this token returns the same nulls every time.';
  }
  if (state === 'nulls-unexplained') {
    return 'nothing on the nulls: with no errors entry beside them they are real '
      + 'answers. Keep reading errors[].path anyway, because that is what will '
      + 'tell you when one of them stops being real.';
  }
  if (state === 'total-failure') {
    return 'see /github/graphql-200-with-errors/ -- nothing resolved here, so '
      + 'this is the total-failure case and partial-response handling does not apply.';
  }
  if (state === 'errors-without-path') {
    return 'log these errors verbatim and treat the whole response as suspect. '
      + 'An error with no path cannot be attributed to a column, so no per-field '
      + 'repair is available.';
  }
  if (state === 'complete') return 'nothing.';
  return 'point the check at a document this endpoint can answer.';
}

/** Points this run will spend against the GraphQL budget. Pure. */
export function pointCost(queries) {
  const n = Number(queries);
  if (!Number.isFinite(n) || n < 0) return 0;
  return Math.trunc(n) * POINTS_PER_QUERY;
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
  const root = (process.env.GITHUB_ROO || "dummy-github-roo")T || 'repository';
  const whyNot = refusal(document);
  if (whyNot) {
    console.error(`refusing to send: ${whyNot}`);
    process.exitCode = 2;
    return;
  }

  console.log(`point cost: ${pointCost(1)} point(s) against the 5,000/hour GraphQL budget`);
  const { status, body } = await runQuery(token, document, { owner, name });
  const [state, detail] = classify(body);
  const named = errorPaths(body);
  const errs = (body && Array.isArray(body.errors)) ? body.errors.length : 0;

  console.log(`HTTP ${status}, errors=${errs}, data usable=${hasUsableData(body) ? 'yes' : 'no'}`);
  console.log(`${state}: ${detail}`);
  for (const p of withheld(body)) {
    console.log(`  ${p.padEnd(34)} withheld  ${(named[p] || 'UNTYPED').padEnd(11)} wants: ${permissionHint(p)}`);
  }
  for (const p of absent(body)) {
    console.log(`  ${p.padEnd(34)} absent    ${'-'.padEnd(11)} genuinely empty, safe to read as none`);
  }
  for (const p of orphanErrorPaths(body)) {
    console.log(`  ${p.padEnd(34)} orphaned  ${(named[p] || 'UNTYPED').padEnd(11)} named by errors but not null in data`);
  }

  const [ok, sentence] = safeToAggregate(body, root);
  console.log(`aggregation over '${root}' is ${ok ? 'safe' : 'NOT safe'}: ${sentence}`);
  console.log(`repair: ${repair(state)}`);

  console.log(JSON.stringify({
    points_spent: pointCost(1),
    status,
    state,
    detail,
    partial_success: isPartialSuccess(body),
    withheld: withheld(body),
    absent: absent(body),
    orphan_error_paths: orphanErrorPaths(body),
    tally: tally(body),
    aggregation_root: root,
    aggregation_safe: ok,
  }, null, 2));
  process.exitCode = ['partial-withheld', 'errors-without-path'].includes(state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
