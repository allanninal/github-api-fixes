/**
 * Resolve every first and last in a GraphQL document against the ceiling of 100.
 *
 * Read only, and queries only. GitHub's GraphQL endpoint takes a document in
 * the request body, so a read is carried by POST there just as a write would
 * be; that is transport, not intent. Any document containing a mutation or a
 * subscription is refused before a socket opens.
 *
 * Environment:
 *   GITHUB_TOKEN      a token with read access. Not needed with GITHUB_OFFLINE.
 *   GITHUB_QUERY      the document as a string
 *   GITHUB_VARIABLES  JSON object of the variables you actually send
 *   GITHUB_REPO       owner/name, to fill the default query
 *   GITHUB_OFFLINE    set to 1 to audit the text only
 */
const API = 'https://api.github.com';
const UA = 'github-graphql-slice/1.0';

/** The ceiling on first and last, on every connection in the schema. */
export const CEILING = 100;

/** A simple query costs one point; a validation rejection costs none. */
export const POINTS_PER_QUERY = 1;

const DEFAULT_QUERY = 'query($owner: String!, $name: String!, $first: Int = 250) {'
  + ' repository(owner: $owner, name: $name) {'
  + ' issues(first: $first, states: OPEN) { totalCount nodes { number title } }'
  + ' } }';

const ARGUMENT_IN_MESSAGE = /Argument '([A-Za-z_][A-Za-z0-9_]*)' on Field '([A-Za-z_][A-Za-z0-9_]*)'/;

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

/** The text written for one named argument, or null. Pure. */
export function argumentValue(argumentText, name) {
  const src = String(argumentText ?? '');
  const parts = [];
  let depth = 0;
  let cur = '';
  for (const ch of src) {
    if ('([{'.includes(ch)) depth += 1;
    else if (')]}'.includes(ch)) depth = Math.max(0, depth - 1);
    if (ch === ',' && depth === 0) { parts.push(cur); cur = ''; continue; }
    cur += ch;
  }
  parts.push(cur);
  for (const part of parts) {
    const at = part.indexOf(':');
    if (at < 0) continue;
    if (part.slice(0, at).trim() === name) {
      return part.slice(at + 1).trim() || null;
    }
  }
  return null;
}

/** Defaults declared in the operation's variable definitions. Pure. */
export function variableDefaults(document) {
  const head = stripNoise(document).split('{')[0];
  const out = {};
  for (const part of head.replace(/\(/g, ' ').replace(/\)/g, ' ').split(',')) {
    const at = part.indexOf(':');
    if (at < 0) continue;
    // The first part still carries the operation keyword and name in front
    // of the variable, so take the last token rather than the whole thing.
    const name = part.slice(0, at).trim().split(/\s+/).pop();
    const rest = part.slice(at + 1);
    if (!name || !name.startsWith('$') || !rest.includes('=')) continue;
    out[name] = rest.slice(rest.indexOf('=') + 1).trim();
  }
  return out;
}

/** Every first and last in the document, with the field carrying it. Pure. */
export function slicingArguments(document) {
  const src = stripNoise(document);
  const out = [];
  let i = 0;
  let depth = 0;
  let word = '';
  while (i < src.length) {
    const ch = src[i];
    if (/[A-Za-z0-9_]/.test(ch)) { word += ch; i += 1; continue; }
    if (ch === '(' && word) {
      const field = word;
      let j = i;
      let level = 0;
      while (j < src.length) {
        if (src[j] === '(') level += 1;
        else if (src[j] === ')') { level -= 1; if (level === 0) break; }
        j += 1;
      }
      const args = src.slice(i + 1, j);
      for (const arg of ['first', 'last']) {
        const raw = argumentValue(args, arg);
        if (raw !== null) out.push({ field, arg, raw, depth });
      }
      i = j + 1;
      word = '';
      continue;
    }
    if (ch === '{') depth += 1;
    else if (ch === '}') depth = Math.max(0, depth - 1);
    word = '';
    i += 1;
  }
  return out;
}

/** An integer, or null if this is not one. Pure. */
export function asInt(value) {
  const text = String(value ?? '').trim();
  if (!/^-?[0-9]+$/.test(text)) return null;
  return Number(text);
}

/** One written slicing value resolved to [value, source]. Pure. */
export function resolveSlice(raw, defaults, variables) {
  const text = String(raw ?? '').trim();
  if (!text) return [null, 'missing'];
  if (!text.startsWith('$')) return [asInt(text), 'literal'];
  const supplied = (variables && typeof variables === 'object') ? variables : {};
  const bare = text.slice(1);
  if (Object.prototype.hasOwnProperty.call(supplied, bare)) {
    return [asInt(supplied[bare]), 'variable-supplied'];
  }
  if (defaults && Object.prototype.hasOwnProperty.call(defaults, text)) {
    return [asInt(defaults[text]), 'variable-default'];
  }
  return [null, 'unresolved'];
}

/** One resolved value against the ceiling. Pure. */
export function verdict(value) {
  if (value === null || value === undefined) return 'unresolved';
  if (value < 1) return 'below-one';
  if (value > CEILING) return 'over-ceiling';
  if (value === CEILING) return 'at-ceiling';
  return 'under-ceiling';
}

/** Round trips at 100 per page for a requested size. Pure. */
export function pagesNeeded(value) {
  if (value === null || value === undefined || value < 1) return null;
  return Math.ceil(value / CEILING);
}

/** Every slicing argument, resolved and judged. Pure. */
export function audit(document, variables) {
  const defaults = variableDefaults(document);
  return slicingArguments(document).map((found) => {
    const [value, source] = resolveSlice(found.raw, defaults, variables);
    return {
      field: found.field,
      arg: found.arg,
      depth: found.depth,
      written: found.raw,
      value,
      source,
      verdict: verdict(value),
      pages: pagesNeeded(value),
    };
  });
}

/** Classify a whole document. Pure. Returns [state, detail]. */
export function classify(findings) {
  if (!findings || findings.length === 0) {
    return ['no-slicing-argument', 'no first or last appears anywhere in this '
      + 'document. GitHub requires a slicing argument on every connection, so '
      + 'either there is no connection here or the query is rejected for a '
      + 'different reason than this note describes.'];
  }
  const over = findings.filter((f) => f.verdict === 'over-ceiling');
  const literal = over.filter((f) => f.source === 'literal');
  if (literal.length) {
    const f = literal[0];
    return ['over-ceiling-in-the-document',
      `${f.field}.${f.arg} asks for ${f.value}, which is over the ceiling of `
      + `${CEILING}, and the number is written in the query.`];
  }
  if (over.length) {
    const f = over[0];
    return ['over-ceiling-through-a-variable',
      `${f.field}.${f.arg} resolves to ${f.value} through a ${f.source}, so a `
      + `search of the document for a number over ${CEILING} finds nothing and `
      + 'every call is still rejected.'];
  }
  const unresolved = findings.filter((f) => f.verdict === 'unresolved');
  if (unresolved.length) {
    const f = unresolved[0];
    return ['unresolved-slice',
      `${f.field}.${f.arg} is written as ${f.written} and no default or `
      + 'supplied value explains it, so this document cannot be cleared from '
      + 'the text alone.'];
  }
  const below = findings.filter((f) => f.verdict === 'below-one');
  if (below.length) {
    const f = below[0];
    return ['slice-below-one',
      `${f.field}.${f.arg} resolves to ${f.value}. The range is 1 to ${CEILING} `
      + 'and zero is rejected the same way an oversized value is.'];
  }
  return ['within-the-ceiling',
    `all ${findings.length} slicing argument(s) resolve to between 1 and `
    + `${CEILING}. This document is not rejected for an argument value; the `
    + 'node count is a separate question.'];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (state === 'over-ceiling-in-the-document') {
    return 'set the value to 100 and page with after: $cursor until '
      + 'pageInfo.hasNextPage is false. The ceiling is not adjustable.';
  }
  if (state === 'over-ceiling-through-a-variable') {
    return 'fix the value where it is set, not in the query text. Cap it at '
      + '100 in the caller or in the variable default, and page with '
      + 'after: $cursor for the rest.';
  }
  if (state === 'unresolved-slice') {
    return 'run this again with the variables so the value can be resolved. An '
      + 'argument nobody can resolve is not an argument anybody has checked.';
  }
  if (state === 'slice-below-one') {
    return 'use a value of at least 1. A slicing argument of 0 is not a cheap '
      + 'query, it is a rejected one.';
  }
  if (state === 'within-the-ceiling') {
    return 'nothing on the argument ceiling. Check the node count as well: see '
      + '/github/graphql-node-limit-exceeded/ -- a document legal on every '
      + 'argument can still be rejected for the product of them.';
  }
  return 'point the check at a document containing a connection.';
}

/** Which phase of the request failed. Pure. */
export function errorPhase(status, body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return 'unreadable';
  if (!(Array.isArray(body.errors) && body.errors.length > 0)) return 'clean';
  if (!Object.prototype.hasOwnProperty.call(body, 'data')) return 'validation';
  return 'execution';
}

/** The [argument, field] the server named, or [null, null]. Pure. */
export function offendingArgument(body) {
  if (!body || typeof body !== 'object' || !Array.isArray(body.errors)) return [null, null];
  for (const err of body.errors) {
    const message = (err && typeof err === 'object' && err.message) || '';
    const m = ARGUMENT_IN_MESSAGE.exec(message);
    if (m) return [m[1], m[2]];
  }
  return [null, null];
}

/** Points this run can spend. Pure. */
export function pointCost(sending) {
  return sending ? POINTS_PER_QUERY : 0;
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
  const offline = (process.env.GITHUB_OFFLINE || "dummy-github-offline") === '1';
  const document = (process.env.GITHUB_QUER || "dummy-github-quer")Y || DEFAULT_QUERY;
  let variables = {};
  try { variables = JSON.parse((process.env.GITHUB_VARIABLE || "dummy-github-variable")S || '{}'); } catch {
    console.error('GITHUB_VARIABLES takes a JSON object');
    process.exitCode = 2;
    return;
  }
  if ((process.env.GITHUB_REPO || "dummy-github-repo")) {
    const [owner, name] = (process.env.GITHUB_REPO || "dummy-github-repo").split('/');
    if (!owner || !name) {
      console.error('GITHUB_REPO takes owner/name');
      process.exitCode = 2;
      return;
    }
    if (!('owner' in variables)) variables.owner = owner;
    if (!('name' in variables)) variables.name = name;
  }

  const whyNot = refusal(document);
  if (whyNot) {
    console.error(`refusing to send: ${whyNot}`);
    process.exitCode = 2;
    return;
  }

  const findings = audit(document, variables);
  console.log(`point cost: ${pointCost(!offline)} point(s). A document rejected `
    + 'during validation never executes and is not billed at all.');
  for (const f of findings) {
    console.log(`  ${f.field}.${f.arg}  written ${f.written}  value `
      + `${f.value === null ? '?' : f.value}  ${f.source}  `
      + `${f.verdict === 'over-ceiling' ? `OVER, needs ${f.pages} pages` : f.verdict}`);
  }
  const [state, detail] = classify(findings);
  console.log(`${state}: ${detail}`);
  console.log(`repair: ${repair(state)}`);

  let probe = null;
  if (!offline) {
    const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
    if (!token) {
      console.error('set GITHUB_TOKEN, or GITHUB_OFFLINE=1 to audit the text only');
      process.exitCode = 2;
      return;
    }
    const { status, body } = await runQuery(token, document, variables);
    const phase = errorPhase(status, body);
    const [arg, field] = offendingArgument(body);
    const hasData = !!body && typeof body === 'object'
      && Object.prototype.hasOwnProperty.call(body, 'data');
    console.log(`HTTP ${status}, phase=${phase}, data key present=${hasData ? 'yes' : 'no'}`);
    if (arg) console.log(`rejected argument: ${arg} on field ${field}`);
    if (phase === 'validation') {
      console.log('validation-rejected: the body carries errors and no data key, '
        + 'which is what a failure before execution looks like');
    }
    probe = { status, phase, rejected_argument: arg, rejected_field: field };
  }

  console.log(JSON.stringify({ ceiling: CEILING, state, findings, probe }, null, 2));
  process.exitCode = (state === 'within-the-ceiling' || state === 'no-slicing-argument') ? 0 : 1;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
