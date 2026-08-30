/**
 * Price GraphQL documents against the per-minute secondary limit.
 *
 * Read only, and queries only. GitHub's GraphQL endpoint takes its document in
 * the request body, so a read travels by POST there exactly as a write would.
 * Every document is parsed first, anything containing a mutation or a
 * subscription is refused before a socket opens, and the only document this
 * script sends is a read query of its own.
 *
 * Against the secondary limit of 2,000 points a minute, a request whose
 * document contains a mutation counts as 5 points and one that does not counts
 * as 1. A write loop therefore reaches the wall at a fifth of the rate a read
 * loop survives, with the separate hourly budget almost untouched.
 *
 * Environment:
 *   GITHUB_TOKEN      a token with read access to the GraphQL API
 *   GITHUB_QUERY      a document to price
 *   GITHUB_RATE       requests a minute the loop actually sends
 *   GITHUB_BATCH      how many rows the job has to get through
 */
const API = 'https://api.github.com';
const UA = 'github-graphql-mutation-budget/1.0';

/** The secondary limit on the GraphQL endpoint, and its two weights. */
export const SECONDARY_POINTS_PER_MINUTE = 2000;
export const WEIGHT_WITH_MUTATION = 5;
export const WEIGHT_WITHOUT_MUTATION = 1;

/** The other bucket entirely, named so the two are never confused. */
export const PRIMARY_POINTS_PER_HOUR = 5000;

/** GitHub asks for at least this long between mutations on one resource. */
export const SAME_RESOURCE_GAP_SECONDS = 1.0;

/** The one document this script ever sends, and it is guarded like any other. */
export const PROBE_QUERY = 'query { rateLimit { limit cost remaining used resetAt } }';

/** This run's own cost against the hourly budget. */
export const POINTS_PER_QUERY = 1;

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
      return `the document contains a ${kind}. This script prices documents, it `
        + 'does not send them: a query is a read, and the section it belongs to '
        + 'promises its scripts never write.';
    }
  }
  return null;
}

/** Secondary-limit points for one request carrying this document. Pure. */
export function weight(document) {
  return operations(document).includes('mutation')
    ? WEIGHT_WITH_MUTATION : WEIGHT_WITHOUT_MUTATION;
}

/** Requests a minute this weight allows before the limit binds. Pure. */
export function ceilingPerMinute(points) {
  const n = Number(points);
  if (!Number.isFinite(n) || n <= 0) return 0;
  return Math.floor(SECONDARY_POINTS_PER_MINUTE / n);
}

/** Seconds between requests implied by the ceiling, one worker. Pure. */
export function minGapSeconds(points) {
  const ceiling = ceilingPerMinute(points);
  return ceiling <= 0 ? 0 : 60 / ceiling;
}

/** What a given request rate costs against the per-minute limit. Pure. */
export function pointsPerMinute(rate, points) {
  const r = Math.max(0, Math.trunc(Number(rate) || 0));
  const p = Math.trunc(Number(points) || 0);
  return r * p;
}

/** How long a batch of this size takes at this rate, in minutes. Pure. */
export function minutesForBatch(count, rate) {
  const r = Math.max(0, Math.trunc(Number(rate) || 0));
  if (r <= 0) return null;
  return Math.ceil(Math.max(0, Math.trunc(Number(count) || 0)) / r);
}

/** Judge a request rate against the per-minute limit. Pure. [state, detail]. */
export function classifyRate(rate, points) {
  const spend = pointsPerMinute(rate, points);
  const ceiling = ceilingPerMinute(points);
  if (!rate) {
    return ['not-measured', 'no rate given, so this document is priced but not '
      + `judged. Its ceiling is ${ceiling} request(s)/minute.`];
  }
  if (spend > SECONDARY_POINTS_PER_MINUTE) {
    return ['over-ceiling', `${rate} request(s)/minute of this document is `
      + `${spend} point(s)/minute against a limit of ${SECONDARY_POINTS_PER_MINUTE}.`];
  }
  if (spend > SECONDARY_POINTS_PER_MINUTE * 0.8) {
    return ['near-ceiling', `${rate} request(s)/minute is ${spend} `
      + `point(s)/minute, inside the limit of ${SECONDARY_POINTS_PER_MINUTE} but `
      + 'with under a fifth of it left.'];
  }
  return ['within-ceiling', `${rate} request(s)/minute is ${spend} `
    + `point(s)/minute against a limit of ${SECONDARY_POINTS_PER_MINUTE}.`];
}

/** Attribute a recorded failure to one bucket or the other. Pure. */
export function classifyThrottle(status, message, graphqlRemaining) {
  const text = String(message ?? '').toLowerCase();
  const secondary = text.includes('secondary rate limit');
  const parsed = Number(graphqlRemaining);
  const remaining = Number.isFinite(parsed) && graphqlRemaining !== null
    && graphqlRemaining !== '' ? parsed : null;
  const healthy = remaining !== null && remaining > PRIMARY_POINTS_PER_HOUR * 0.1;

  if (secondary && healthy) {
    return ['secondary-not-budget', `a secondary rate limit with ${remaining} `
      + 'point(s) still in the hourly budget. This is the per-minute ceiling, '
      + 'and no amount of waiting for the hourly reset will help.'];
  }
  if (secondary) {
    return ['secondary-limit', 'a secondary rate limit. The hourly budget was '
      + 'not readable or was itself low, so slow down and check both.'];
  }
  if (text.includes('rate limit') && remaining === 0) {
    return ['primary-exhausted', 'the hourly point budget is spent. That is a '
      + 'different bucket with a different note and it refills on a schedule.'];
  }
  if (text.includes('rate limit')) {
    return ['rate-limited-unclassified', 'a rate-limit message that does not '
      + 'name the secondary limit. Read resources.graphql at the moment of '
      + 'failure to attribute it.'];
  }
  if (['403', '429'].includes(String(status))) {
    return ['forbidden-not-throttled', `HTTP ${status} with no rate-limit `
      + 'wording, so this is a permission problem rather than a throttle.'];
  }
  return ['no-throttle', 'nothing in this record names a rate limit.'];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (state === 'over-ceiling') {
    return 'batch mutations into one document, serialise the loop, and cap it '
      + `at ${ceilingPerMinute(WEIGHT_WITH_MUTATION)}/minute or below. The 5 `
      + 'points are charged per request, so fewer requests is the whole lever.';
  }
  if (state === 'near-ceiling') {
    return 'leave headroom. A retry, a redeploy or one extra worker puts this '
      + 'over, and the limit gives no warning before it binds.';
  }
  if (state === 'secondary-not-budget') {
    return 'rate-limit the writer against points a minute, not requests a '
      + 'minute, and honour retry-after. Do not rewrite the retry logic around '
      + 'the hourly budget; that bucket was fine.';
  }
  if (state === 'secondary-limit') {
    return 'slow the writer down and record resources.graphql at the moment of '
      + 'failure so the next one can be attributed.';
  }
  if (state === 'primary-exhausted') {
    return 'see /github/graphql-rate-limited/ -- the hourly point budget is a '
      + 'different bucket and this is not the note for it.';
  }
  if (state === 'within-ceiling') {
    return 'nothing on the point arithmetic. Check concurrency and the '
      + 'one-second gap between mutations on the same resource separately; '
      + 'points do not express either.';
  }
  return 'supply the rate the loop actually runs at, or the failure you '
    + 'recorded, and the arithmetic becomes a verdict.';
}

/** Everything this script knows about one document. Pure. */
export function price(label, document, rate) {
  const ops = operations(document);
  const points = weight(document);
  const [state, detail] = classifyRate(rate, points);
  return {
    document: label,
    operations: ops,
    points_per_request: points,
    ceiling_per_minute: ceilingPerMinute(points),
    min_gap_seconds: Number(minGapSeconds(points).toFixed(4)),
    points_per_minute_at_rate: pointsPerMinute(rate, points),
    not_sent: refusal(document),
    state,
    detail,
    repair: repair(state),
  };
}

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'Content-Type': 'application/json',
    'User-Agent': UA,
  };
}

async function runQuery(token, document) {
  const res = await fetch(`${API}/graphql`, {
    // A GraphQL query is a read. POST is only how the document reaches the
    // endpoint, and refusal() has already rejected anything that is not a read.
    method: 'POST',
    headers: headers(token),
    body: JSON.stringify({ query: document, variables: {} }),
  });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return { status: res.status, body };
}

async function graphqlBudget(token) {
  const res = await fetch(`${API}/rate_limit`, { headers: headers(token) });
  if (!res.ok) return null;
  try {
    const body = await res.json();
    return (body.resources || {}).graphql || null;
  } catch { return null; }
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const document = (process.env.GITHUB_QUERY || "dummy-github-query");
  if (!token || !document) {
    console.error('set GITHUB_TOKEN (read-only is enough) and GITHUB_QUERY');
    process.exitCode = 2;
    return;
  }
  const rate = Number((process.env.GITHUB_RAT || "dummy-github-rat")E || 0);
  const batch = Number((process.env.GITHUB_BATC || "dummy-github-batc")H || 0);

  console.log(`point cost: ${POINTS_PER_QUERY} point(s) against the `
    + `${PRIMARY_POINTS_PER_HOUR}/hour GraphQL budget`);

  const budget = await graphqlBudget(token);
  if (budget) {
    console.log(`graphql budget: ${budget.remaining}/${budget.limit} remaining`);
  }
  if (refusal(PROBE_QUERY) === null) {
    const { status } = await runQuery(token, PROBE_QUERY);
    console.log(`probe read query: HTTP ${status}, ${POINTS_PER_QUERY} point(s) spent`);
  }

  const p = price('GITHUB_QUERY', document, rate);
  console.log(`${p.document}: operations=${p.operations.join(', ') || 'none'} -> `
    + `${p.points_per_request} point(s) per request`);
  if (p.not_sent) console.log(`  not sent: ${p.not_sent}`);
  console.log(`  ceiling ${p.ceiling_per_minute} request(s)/minute, minimum gap `
    + `${p.min_gap_seconds.toFixed(3)}s on one worker`);
  console.log(`  ${p.state}: ${p.detail}`);
  if (batch) {
    console.log(`  ${batch} row(s) takes at least `
      + `${minutesForBatch(batch, p.ceiling_per_minute)} minute(s) at the ceiling`);
  }
  console.log(`  repair: ${p.repair}`);

  console.log(JSON.stringify({
    points_spent: POINTS_PER_QUERY,
    secondary_points_per_minute: SECONDARY_POINTS_PER_MINUTE,
    graphql_budget: budget,
    documents: [p],
  }, null, 2));
  process.exitCode = ['over-ceiling', 'near-ceiling'].includes(p.state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
