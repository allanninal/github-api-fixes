/**
 * Measure what a GraphQL query costs and compare it with what anybody assumed.
 *
 * Read only, and queries only. GitHub's GraphQL endpoint takes a document in
 * the request body, so a read is carried by POST there just as a write would
 * be; that is transport, not intent. Any document containing a mutation or a
 * subscription is refused before a socket opens. The baseline file is printed
 * for you to update rather than rewritten here.
 *
 * Environment:
 *   GITHUB_TOKEN      a token with read access to the GraphQL API
 *   GITHUB_QUERY      the document as a string
 *   GITHUB_VARIABLES  JSON object of variables
 *   GITHUB_LOGIN      user or organisation for the default query
 *   GITHUB_ASSUMED    the cost somebody believes this has
 *   GITHUB_CALLS      how often this shape is sent, for an hourly projection
 */
const API = 'https://api.github.com';
const UA = 'github-graphql-cost/1.0';

export const POINTS_PER_QUERY = 1;

/** The selection that makes a response report its own price. */
export const RATE_LIMIT_SELECTION = 'rateLimit { cost nodeCount limit remaining resetAt }';

const DEFAULT_QUERY = 'query($login: String!) {'
  + ' repositoryOwner(login: $login) {'
  + ' repositories(first: 50) { totalCount nodes { name'
  + ' issues(first: 20, states: OPEN) { totalCount nodes { number } } } }'
  + ' } }';

/** Comments and string literals replaced by spaces. Length preserving. Pure. */
export function blankNoise(document) {
  const src = String(document ?? '');
  const out = src.split('');
  let i = 0;
  while (i < src.length) {
    const ch = src[i];
    if (ch === '#') {
      while (i < src.length && src[i] !== '\n') { out[i] = ' '; i += 1; }
      continue;
    }
    if (src.startsWith('"""', i)) {
      const j = src.indexOf('"""', i + 3);
      const end = j < 0 ? src.length : j + 3;
      for (let k = i; k < end; k += 1) out[k] = ' ';
      i = end;
      continue;
    }
    if (ch === '"') {
      out[i] = ' ';
      i += 1;
      while (i < src.length && src[i] !== '"') {
        const step = src[i] === '\\' ? 2 : 1;
        for (let k = i; k < Math.min(src.length, i + step); k += 1) out[k] = ' ';
        i += step;
      }
      if (i < src.length) out[i] = ' ';
      i += 1;
      continue;
    }
    i += 1;
  }
  return out.join('');
}

/** The top-level operations in a document, in order. Pure. */
export function operations(document) {
  const src = `${blankNoise(document)} `;
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

/** Index of the operation's opening brace, or -1. Pure. */
export function selectionSetStart(document) {
  const src = blankNoise(document);
  let parens = 0;
  for (let i = 0; i < src.length; i += 1) {
    const ch = src[i];
    if (ch === '(') parens += 1;
    else if (ch === ')') parens = Math.max(0, parens - 1);
    else if (ch === '{' && parens === 0) return i;
  }
  return -1;
}

/** The document with rateLimit added to its top-level selection. Pure. */
export function injectRateLimit(document) {
  const src = String(document ?? '');
  if (blankNoise(src).includes('rateLimit')) return src;
  const at = selectionSetStart(src);
  if (at < 0) return src;
  return `${src.slice(0, at + 1)} ${RATE_LIMIT_SELECTION}${src.slice(at + 1)}`;
}

/** The first and last arguments in one argument list. Pure. */
export function slicingPairs(argumentText) {
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
  const out = [];
  for (const part of parts) {
    const at = part.indexOf(':');
    if (at < 0) continue;
    const key = part.slice(0, at).trim();
    if (key === 'first' || key === 'last') out.push([key, part.slice(at + 1).trim()]);
  }
  return out;
}

/** Defaults declared in the operation's variable definitions. Pure. */
export function variableDefaults(document) {
  const head = blankNoise(document).split('{')[0];
  const out = {};
  for (const part of head.replace(/\(/g, ' ').replace(/\)/g, ' ').split(',')) {
    const at = part.indexOf(':');
    if (at < 0) continue;
    const name = part.slice(0, at).trim().split(/\s+/).pop();
    const rest = part.slice(at + 1);
    if (!name || !name.startsWith('$') || !rest.includes('=')) continue;
    out[name] = rest.slice(rest.indexOf('=') + 1).trim();
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

/** Every first and last in the document, resolved. Pure. */
export function sliceValues(document, variables) {
  const src = blankNoise(document);
  const defaults = variableDefaults(document);
  const out = [];
  let i = 0;
  let word = '';
  let field = '';
  while (i < src.length) {
    const ch = src[i];
    if (/[A-Za-z0-9_]/.test(ch)) { word += ch; i += 1; continue; }
    if (word) { field = word; word = ''; }
    if (ch === '(') {
      let j = i;
      let level = 0;
      while (j < src.length) {
        if (src[j] === '(') level += 1;
        else if (src[j] === ')') { level -= 1; if (level === 0) break; }
        j += 1;
      }
      for (const [arg, raw] of slicingPairs(src.slice(i + 1, j))) {
        const [value, source] = resolveSlice(raw, defaults, variables);
        out.push({ field, arg, written: raw, value, source });
      }
      i = j + 1;
      continue;
    }
    i += 1;
  }
  return out;
}

/** The documented approximation, from the text. Pure. Returns [points, unresolved]. */
export function predictedCost(document, variables) {
  const values = sliceValues(document, variables);
  let total = 0;
  let unresolved = 0;
  for (const v of values) {
    if (Number.isInteger(v.value) && v.value > 0) total += v.value;
    else unresolved += 1;
  }
  return [Math.max(1, Math.ceil(total / 100)), unresolved];
}

/** The rateLimit object anywhere in a response. Pure. */
export function findRateLimit(body) {
  if (Array.isArray(body)) {
    for (const item of body) {
      const found = findRateLimit(item);
      if (found !== null) return found;
    }
    return null;
  }
  if (!body || typeof body !== 'object') return null;
  if (body.rateLimit && typeof body.rateLimit === 'object') return body.rateLimit;
  for (const value of Object.values(body)) {
    const found = findRateLimit(value);
    if (found !== null) return found;
  }
  return null;
}

/** What the server charged for this call, or null. Pure. */
export function measuredCost(body) {
  const node = findRateLimit(body) || {};
  return Number.isInteger(node.cost) ? node.cost : null;
}

/** The node count the server computed for this call, or null. Pure. */
export function measuredNodes(body) {
  const node = findRateLimit(body) || {};
  return Number.isInteger(node.nodeCount) ? node.nodeCount : null;
}

/** How many items actually came back in every nodes list. Pure. */
export function returnedNodes(body) {
  let total = 0;
  if (Array.isArray(body)) {
    for (const item of body) total += returnedNodes(item);
    return total;
  }
  if (!body || typeof body !== 'object') return 0;
  for (const [key, value] of Object.entries(body)) {
    if ((key === 'nodes' || key === 'edges') && Array.isArray(value)) total += value.length;
    total += returnedNodes(value);
  }
  return total;
}

/** The disagreement between the text and the server. Pure. */
export function gap(predicted, measured) {
  if (measured === null || measured === undefined) return [null, 'unmeasured'];
  if (!predicted || predicted <= 0) return [null, 'unpredictable'];
  const ratio = measured / predicted;
  if (ratio >= 2) return [ratio, 'far-above-the-text'];
  if (ratio > 1.25) return [ratio, 'above-the-text'];
  if (ratio < 0.75) return [ratio, 'below-the-text'];
  return [ratio, 'close-to-the-text'];
}

/** This shape's price against the recorded one. Pure. */
export function drift(baseline, measured) {
  if (!Number.isInteger(baseline)) {
    return ['no-baseline', 'no recorded cost for this shape, so nothing can be '
      + 'compared. Record this one and the next change becomes visible.'];
  }
  if (measured === null || measured === undefined) {
    return ['unmeasured', 'nothing to compare the baseline against.'];
  }
  if (measured === baseline) {
    return ['unchanged', `this shape costs the same ${baseline} point(s) it did `
      + 'when the baseline was written.'];
  }
  const direction = measured > baseline ? 'rise' : 'fall';
  const percent = (Math.abs(measured - baseline) * 100) / Math.max(1, baseline);
  return [measured > baseline ? 'increased' : 'decreased',
    `this shape cost ${baseline} point(s) when the baseline was written and `
    + `costs ${measured} now, a ${direction} of ${percent.toFixed(0)}%.`];
}

/** Classify one measurement. Pure. Returns [state, detail]. */
export function classify(measured, predicted, baseline, returned) {
  if (measured === null || measured === undefined) {
    return ['cost-unmeasured', 'the response carried no rateLimit { cost }, so '
      + 'this run measured nothing. Nothing else here is worth reading.'];
  }
  const [driftState, driftDetail] = drift(baseline, measured);
  if (driftState === 'increased') return ['cost-increased-since-the-baseline', driftDetail];
  const [ratio, verdict] = gap(predicted, measured);
  if (verdict === 'far-above-the-text' || verdict === 'above-the-text') {
    return ['cost-above-the-shape-of-the-query',
      `the server charged ${measured} where the document predicted ${predicted}, `
      + `a factor of ${ratio.toFixed(1)}.`];
  }
  if (Number.isInteger(returned) && measured >= 5 && returned <= measured) {
    return ['cost-unrelated-to-the-data-returned',
      `${returned} node(s) came back for ${measured} point(s). The price follows `
      + 'what the query asked for, not what it found.'];
  }
  return ['cost-measured', `this shape costs ${measured} point(s), which is what `
    + 'the document predicts and what the baseline says.'];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (state === 'cost-increased-since-the-baseline') {
    return 'record the new cost against the shape and treat the change as part '
      + 'of the diff that caused it. A price change belongs in a code review, '
      + 'not in an incident.';
  }
  if (state === 'cost-above-the-shape-of-the-query') {
    return 'find what the document traverses that the arithmetic did not see -- '
      + 'usually a connection nested inside another -- and split the query '
      + 'rather than widening the budget.';
  }
  if (state === 'cost-unrelated-to-the-data-returned') {
    return 'lower the first values rather than filtering harder. Filters change '
      + 'what comes back; only the slice changes the price.';
  }
  if (state === 'cost-unmeasured') {
    return 'add rateLimit { cost nodeCount remaining } to the query. It costs no '
      + 'extra round trip and there is no other way to learn the number.';
  }
  if (state === 'cost-measured') {
    return 'record this number so the next change to the query has something to '
      + 'be compared against.';
  }
  return 'point the check at a document this endpoint can answer.';
}

/** What a schedule spends. Pure. */
export function pointsPerHour(cost, callsPerHour) {
  if (!Number.isInteger(cost) || !callsPerHour) return null;
  return cost * Number(callsPerHour);
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
  }

  const whyNot = refusal(document);
  if (whyNot) {
    console.error(`refusing to send: ${whyNot}`);
    process.exitCode = 2;
    return;
  }

  console.log(`point cost: ${pointCost(1)} point(s) against the 5,000/hour GraphQL budget`);
  const [predicted, unresolved] = predictedCost(document, variables);
  const slices = sliceValues(document, variables);
  const asked = slices.reduce((a, v) => a + (Number.isInteger(v.value) ? v.value : 0), 0);
  console.log(`predicted from the text: ${predicted} point(s) from ${slices.length} `
    + `slicing argument(s) totalling ${asked}`);
  if (unresolved) {
    console.log(`${unresolved} slicing argument(s) could not be resolved, so the `
      + 'prediction is a lower bound');
  }

  const { body } = await runQuery(token, injectRateLimit(document), variables);
  if (body && Array.isArray(body.errors) && body.errors.length) {
    console.error(`the query itself failed: ${JSON.stringify(body.errors).slice(0, 400)}`);
    process.exitCode = 2;
    return;
  }

  const measured = measuredCost(body);
  const nodes = measuredNodes(body);
  const returned = returnedNodes(body && body.data);
  const assumed = (process.env.GITHUB_ASSUMED || "dummy-github-assumed") ? Number((process.env.GITHUB_ASSUMED || "dummy-github-assumed")) : null;
  console.log(`measured by the server: ${measured === null ? '?' : measured} point(s), `
    + `nodeCount ${nodes === null ? '?' : nodes}`);
  if (assumed !== null) console.log(`assumed by the caller: ${assumed} point(s)`);

  const [state, detail] = classify(measured, predicted, null, returned);
  console.log(`${state}: ${detail}`);
  if (measured !== null) {
    console.log(`${returned} node(s) came back for ${measured} point(s), so the `
      + 'price is not the size of the answer');
  }
  const projected = pointsPerHour(measured, Number((process.env.GITHUB_CALL || "dummy-github-call")S || 0));
  if (projected) {
    console.log(`at ${(process.env.GITHUB_CALLS || "dummy-github-calls")} call(s)/hour this shape needs `
      + `${projected} points/hour. What that means against your quota is `
      + '/github/graphql-rate-limited/');
  }
  console.log(`repair: ${repair(state)}`);

  console.log(JSON.stringify({
    points_spent: pointCost(1), state, predicted, measured, assumed,
    node_count: nodes, returned_nodes: returned, points_per_hour: projected, slices,
  }, null, 2));
  process.exitCode = ['cost-increased-since-the-baseline',
    'cost-above-the-shape-of-the-query',
    'cost-unrelated-to-the-data-returned'].includes(state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
