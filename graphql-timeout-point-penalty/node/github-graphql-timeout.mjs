/**
 * Measure what a timed-out GraphQL query is charged, without retrying it.
 *
 * Read only, and queries only. GitHub's GraphQL endpoint takes a document in
 * the request body, so a read is carried by POST there just as a write would
 * be; that is transport, not intent. Any document containing a mutation or a
 * subscription is refused before a socket opens, and nothing is ever retried.
 *
 * Environment:
 *   GITHUB_TOKEN        a token with read access to the GraphQL API
 *   GITHUB_LOGIN        user or organisation for the default query
 *   GITHUB_QUERY        the document as a string
 *   GITHUB_VARIABLES    JSON object of variables
 *   GITHUB_NORMAL_COST  what this query costs when it finishes
 *   GITHUB_RETRIES      how many retries to price. Nothing is retried.
 */
const API = 'https://api.github.com';
const UA = 'github-graphql-timeout/1.0';

/** The server-side cutoff. A longer client timeout changes nothing. */
export const TIMEOUT_SECONDS = 10;

export const POINTS_PER_QUERY = 1;

/** A call using this much of the cutoff is a finding on a successful run. */
export const NEAR_LIMIT = 0.7;

/** Substrings GitHub uses when it kills a query for time. */
export const TIMEOUT_MARKERS = [
  'timeout', 'timed out', 'took too long', 'respond in time', 'responding in time',
];

const DEFAULT_QUERY = 'query($login: String!, $repos: Int = 100, $prs: Int = 40) {'
  + ' repositoryOwner(login: $login) {'
  + ' repositories(first: $repos, orderBy: {field: PUSHED_AT, direction: DESC}) {'
  + ' nodes { name pullRequests(first: $prs, states: OPEN) {'
  + ' nodes { number title comments(first: 20) { totalCount nodes { createdAt } } }'
  + ' } } } } }';

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

/** One bucket out of a GET /rate_limit body. Pure. */
export function bucketReading(payload, name = 'graphql') {
  if (!payload || typeof payload !== 'object') return null;
  const resources = payload.resources;
  if (!resources || typeof resources !== 'object') return null;
  const bucket = resources[name];
  if (!bucket || typeof bucket !== 'object') return null;
  return {
    limit: bucket.limit, used: bucket.used, remaining: bucket.remaining, reset: bucket.reset,
  };
}

/** Points spent between two readings. Pure. Returns [points, state]. */
export function charged(before, after) {
  if (!before || typeof before !== 'object' || !after || typeof after !== 'object') {
    return [null, 'unreadable'];
  }
  if (before.reset !== after.reset) return [null, 'window-reset'];
  if (!Number.isInteger(before.used) || !Number.isInteger(after.used)) {
    return [null, 'unreadable'];
  }
  if (after.used < before.used) return [null, 'window-reset'];
  return [after.used - before.used, 'measured'];
}

/** The charge with a known background drain removed. Pure. */
export function netCharge(delta, background) {
  if (!Number.isInteger(delta)) return null;
  if (!Number.isInteger(background) || background <= 0) return delta;
  return Math.max(0, delta - background);
}

/** The message GitHub returned, or null. Pure. */
export function timeoutMessage(body) {
  if (!body || typeof body !== 'object') return null;
  if (Array.isArray(body.errors)) {
    for (const err of body.errors) {
      if (err && typeof err === 'object' && err.message) return String(err.message);
    }
  }
  if (body.message) return String(body.message);
  return null;
}

/** Whether this response is the server giving up on time. Pure. */
export function looksLikeTimeout(status, body) {
  if (status === 502 || status === 504) return true;
  const message = (timeoutMessage(body) || '').toLowerCase();
  return TIMEOUT_MARKERS.some((marker) => message.includes(marker));
}

/** Whether the elapsed time agrees with the documented cutoff. Pure. */
export function timingConsistent(elapsed) {
  if (typeof elapsed !== 'number' || Number.isNaN(elapsed)) return false;
  return elapsed >= TIMEOUT_SECONDS * 0.8;
}

/** How much of the cutoff this call used, as a fraction. Pure. */
export function headroom(elapsed) {
  if (typeof elapsed !== 'number' || Number.isNaN(elapsed) || elapsed < 0) return null;
  return elapsed / TIMEOUT_SECONDS;
}

/** Points charged above what the query would have cost. Pure. */
export function penalty(points, normalCost) {
  if (!Number.isInteger(points) || !Number.isInteger(normalCost)) return null;
  return points - normalCost;
}

/** What retrying this document would spend for nothing. Pure. */
export function retryProjection(points, retries) {
  if (!Number.isInteger(points) || !retries || retries < 1) return 0;
  return points * Number(retries);
}

/** Classify one attempt. Pure. Returns [state, detail]. */
export function classify(status, elapsed, points, normalCost, background = 0, body = null) {
  const timedOut = looksLikeTimeout(status, body);
  if (points === null || points === undefined) {
    return ['charge-not-measurable', 'the two rate-limit readings do not support '
      + 'a subtraction, so what this call cost cannot be stated'
      + `${timedOut ? ' -- and it did time out' : ''}.`];
  }
  if (timedOut && Number.isInteger(background) && background > 0) {
    return ['timed-out-charge-not-attributable',
      `the query was killed and ${points} point(s) moved, but the bucket was `
      + 'already draining with nothing sent, so the charge belongs to more than '
      + 'this call.'];
  }
  const extra = penalty(points, normalCost);
  if (timedOut && Number.isInteger(extra) && extra > 0) {
    return ['timed-out-and-charged-extra',
      `the query was killed at the ${TIMEOUT_SECONDS}s cutoff and cost ${points} `
      + `point(s) against a normal cost of ${normalCost}, a penalty of ${extra} point(s).`];
  }
  if (timedOut) {
    return ['timed-out-charge-not-proved',
      `the query was killed at the ${TIMEOUT_SECONDS}s cutoff and the bucket `
      + `moved by ${points} point(s), which is not more than its normal cost. `
      + 'The timeout is real; the penalty is not demonstrated by this run.'];
  }
  const fraction = headroom(elapsed);
  if (fraction !== null && fraction >= NEAR_LIMIT) {
    return ['close-to-the-timeout',
      `the query returned, in ${elapsed.toFixed(1)}s, which is `
      + `${Math.round(fraction * 100)}% of the ${TIMEOUT_SECONDS}s cutoff. This `
      + 'one is one busy repository away from the failure above.'];
  }
  return ['completed-inside-the-limit',
    `the query returned in ${(elapsed || 0).toFixed(1)}s and was charged `
    + `${points} point(s), which is the ordinary case.`];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (state === 'timed-out-and-charged-extra') {
    return 'lower the first values and split the nested connections. Do not '
      + 'retry a timed-out query: the same document reproduces the timeout and '
      + 'the penalty.';
  }
  if (state === 'timed-out-charge-not-proved') {
    return 'make the query smaller anyway. The timeout is the finding; whether '
      + 'this particular run demonstrated the extra charge does not change the '
      + 'repair.';
  }
  if (state === 'timed-out-charge-not-attributable') {
    return 're-run this when nothing else is holding the token, or give the job '
      + 'its own token. A shared bucket cannot attribute a charge to a call.';
  }
  if (state === 'charge-not-measurable') {
    return 're-run it away from the top of the hour, when the window is less '
      + 'likely to reset between the two readings.';
  }
  if (state === 'close-to-the-timeout') {
    return 'shrink it now rather than after the outage. Fewer nested '
      + 'connections and lower first values, paginated.';
  }
  if (state === 'completed-inside-the-limit') {
    return 'nothing here. Keep the elapsed time in your logs so the day it '
      + 'starts climbing is visible before the day it fails.';
  }
  return 'point the check at a document this endpoint can answer.';
}

/** Points this run will spend before any penalty. Pure. */
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

async function readBucket(token) {
  const res = await fetch(`${API}/rate_limit`, { headers: headers(token) });
  if (res.status === 401) throw new Error('401 from GitHub: GITHUB_TOKEN is missing or revoked');
  try { return bucketReading(await res.json()); } catch { return null; }
}

async function runQuery(token, document, variables) {
  const started = Date.now();
  const res = await fetch(`${API}/graphql`, {
    // A GraphQL query is a read. POST is only how the document reaches the
    // endpoint, and refusal() has already rejected anything that is not a read.
    method: 'POST',
    headers: headers(token),
    body: JSON.stringify({ query: document, variables: variables || {} }),
  });
  const elapsed = (Date.now() - started) / 1000;
  let body = null;
  try { body = await res.json(); } catch { body = { message: 'no JSON body' }; }
  return { status: res.status, body, elapsed };
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

  const normalCost = Number((process.env.GITHUB_NORMAL_COS || "dummy-github-normal-cos")T || 1);
  const retries = Number((process.env.GITHUB_RETRIE || "dummy-github-retrie")S || 3);
  console.log(`point cost: up to ${pointCost(1)} point(s) for the query plus `
    + 'whatever the timeout penalty adds, which is the number this run '
    + 'measures. Both /rate_limit reads are free.');

  const idleBefore = await readBucket(token);
  const idleAfter = await readBucket(token);
  let [background, idleState] = charged(idleBefore, idleAfter);
  if (idleState === 'measured') {
    console.log(`idle check: graphql used ${idleBefore.used} -> ${idleAfter.used} `
      + `with nothing sent, so the bucket is ${background === 0 ? 'quiet' : 'already draining'}`);
  } else {
    console.log(`idle check: ${idleState}, so the background drain is unknown`);
    background = 0;
  }

  console.log('sending one query. This script never retries a timed-out query.');
  const { status, body, elapsed } = await runQuery(token, document, variables);
  const after = await readBucket(token);

  const message = timeoutMessage(body);
  console.log(`HTTP ${status} after ${elapsed.toFixed(1)}s`
    + `${message ? `: ${message.slice(0, 160)}` : ''}`);
  const [delta, chargeState] = charged(idleAfter, after);
  const points = netCharge(delta, background);
  if (chargeState === 'measured') {
    console.log(`graphql used ${idleAfter.used} -> ${after.used}, so this call `
      + `was charged ${points} point(s)`);
  } else {
    console.log(`the charge could not be measured: ${chargeState}`);
  }

  const [state, detail] = classify(status, elapsed, points, normalCost, background, body);
  console.log(`${state}: ${detail}`);
  if (timingConsistent(elapsed) && looksLikeTimeout(status, body)) {
    console.log(`the elapsed time agrees with the documented ${TIMEOUT_SECONDS}s cutoff`);
  }
  const projected = retryProjection(points, retries);
  if (projected && state.startsWith('timed-out')) {
    console.log(`${retries} retries of this document would spend ${projected} `
      + 'more point(s) and return nothing');
  }
  console.log(`repair: ${repair(state)}`);

  console.log(JSON.stringify({
    status,
    elapsed_seconds: Number(elapsed.toFixed(2)),
    headroom: headroom(elapsed),
    charged: points,
    background_drain: background,
    normal_cost: normalCost,
    penalty: penalty(points, normalCost),
    retry_cost: projected,
    state,
    detail,
  }, null, 2));
  process.exitCode = (state.startsWith('timed-out') || state === 'close-to-the-timeout') ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
