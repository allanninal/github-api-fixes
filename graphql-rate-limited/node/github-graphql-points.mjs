/**
 * Read the GraphQL point budget, which is not the REST one.
 *
 * Read only. The default run spends nothing: GET /rate_limit reports both
 * buckets and is documented not to count against either. The optional in-band
 * probe sends one query and costs one point, and it says so first.
 *
 * Queries only. GitHub's GraphQL endpoint takes a document in the request body,
 * so a read is carried by POST there just as a write would be; that is
 * transport, not intent. Any document containing a mutation or a subscription
 * is refused before a socket opens.
 *
 * Environment:
 *   GITHUB_TOKEN     a token with read access to the GraphQL API
 *   GITHUB_IN_BAND   set to spend one point measuring a query cost directly
 *   GITHUB_QUERY     measure this document instead of the minimal probe
 *   GITHUB_COST      the cost of one of your queries, if you already know it
 */
const API = 'https://api.github.com';
const UA = 'github-graphql-points/1.0';

/** The published hourly point budgets, keyed by the actor they belong to. */
export const BUDGETS = {
  5000: 'a user token',
  1000: 'the GITHUB_TOKEN issued to a GitHub Actions workflow',
  10000: 'an Enterprise Cloud token',
};

const BUDGET_QUERY = 'query { rateLimit { limit cost remaining used resetAt nodeCount } }';

/** Below this fraction of budget left, slow down rather than discover zero. */
export const TIGHT = 0.2;

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

/** One resource object out of GET /rate_limit. Pure. null if absent. */
export function bucket(rateLimitBody, name) {
  if (!rateLimitBody || typeof rateLimitBody !== 'object') return null;
  const res = rateLimitBody.resources;
  if (!res || typeof res !== 'object') return null;
  const b = res[name];
  return b && typeof b === 'object' ? b : null;
}

/** How much of a bucket is gone, 0..1. Pure. null if unreadable. */
export function usedFraction(b) {
  if (!b || typeof b !== 'object') return null;
  const limit = Number(b.limit);
  const remaining = Number(b.remaining);
  if (!Number.isFinite(limit) || !Number.isFinite(remaining) || limit <= 0) return null;
  return Math.max(0, Math.min(1, (limit - remaining) / limit));
}

/** Seconds until this bucket refills. Pure. null if unreadable. */
export function secondsToReset(b, now) {
  if (!b || typeof b !== 'object') return null;
  const reset = Number(b.reset);
  const t = Number(now);
  if (!Number.isFinite(reset) || !Number.isFinite(t)) return null;
  return Math.max(0, Math.trunc(reset) - Math.trunc(t));
}

/** Which actor an observed hourly limit implies. Pure. */
export function identifyBudget(limit) {
  const n = Number(limit);
  if (!Number.isFinite(n)) return 'an unreadable limit';
  if (Object.prototype.hasOwnProperty.call(BUDGETS, String(Math.trunc(n)))) {
    return BUDGETS[String(Math.trunc(n))];
  }
  return `a limit of ${Math.trunc(n)}, which matches none of the published `
    + 'budgets. Read it as the truth and plan against it.';
}

/** How many more queries of this shape fit in what is left. Pure. */
export function queriesLeft(remaining, costPerQuery) {
  const rem = Number(remaining);
  const cost = Number(costPerQuery);
  if (!Number.isFinite(rem) || !Number.isFinite(cost) || cost <= 0) return null;
  return Math.max(0, Math.floor(rem / cost));
}

/** Queries per hour this budget supports at a measured cost. Pure. */
export function sustainableRate(limit, costPerQuery) {
  const lim = Number(limit);
  const cost = Number(costPerQuery);
  if (!Number.isFinite(lim) || !Number.isFinite(cost) || cost <= 0 || lim <= 0) return null;
  return Math.floor(lim / cost);
}

/** The gap to leave between queries to stay inside the budget. Pure. */
export function secondsBetween(limit, costPerQuery) {
  const rate = sustainableRate(limit, costPerQuery);
  if (!rate) return null;
  return Math.round((3600 / rate) * 10) / 10;
}

/** The type of every entry in a GraphQL errors array. Pure. */
export function errorTypes(body) {
  if (!body || typeof body !== 'object' || !Array.isArray(body.errors)) return [];
  return body.errors.map((e) => (e && typeof e === 'object' && e.type) || 'UNTYPED');
}

/** Whether a GraphQL envelope reports the budget as spent. Pure. */
export function isRateLimited(body) {
  return errorTypes(body).includes('RATE_LIMITED');
}

/** The cost this query reported for itself. Pure. null if not asked for. */
export function inBandCost(body) {
  if (!body || typeof body !== 'object') return null;
  const data = body.data;
  if (!data || typeof data !== 'object') return null;
  const rl = data.rateLimit;
  if (!rl || typeof rl !== 'object') return null;
  const cost = Number(rl.cost);
  return Number.isFinite(cost) ? Math.trunc(cost) : null;
}

/** Compare the two buckets. Pure. Returns [state, detail]. */
export function classify(graphqlB, coreB) {
  const g = usedFraction(graphqlB);
  const c = usedFraction(coreB);
  if (g === null) {
    return ['unreadable', 'resources.graphql was not present in the response, '
      + 'so the GraphQL budget cannot be read from it.'];
  }
  const gLeft = 1 - g;
  const cLeft = c === null ? null : 1 - c;
  const gEmpty = Number(graphqlB.remaining) === 0;
  const cEmpty = cLeft !== null && Number(coreB.remaining) === 0;

  if (gEmpty && cEmpty) {
    return ['both-exhausted', 'both buckets are empty, so this is not the '
      + 'confusing case: everything fails and everything is meant to.'];
  }
  if (gEmpty) {
    return ['graphql-exhausted-rest-healthy',
      `the GraphQL bucket is empty while core is at ${Math.round((cLeft || 0) * 100)}% `
      + 'remaining, so a REST health check reports green on a dead integration.'];
  }
  if (cEmpty) {
    return ['rest-exhausted-graphql-healthy',
      `core is empty and the GraphQL budget is at ${Math.round(gLeft * 100)}% `
      + 'remaining. That is the REST hourly quota, not this one.'];
  }
  if (gLeft < TIGHT) {
    return ['graphql-tight', `${Math.round(gLeft * 100)}% of the GraphQL budget `
      + 'is left, which is close enough that the next burst decides it.'];
  }
  return ['both-healthy', `${Math.round(gLeft * 100)}% of the GraphQL budget and `
    + `${cLeft === null ? 'an unknown amount' : `${Math.round(cLeft * 100)}%`} of core are left.`];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (state === 'graphql-exhausted-rest-healthy') {
    return 'throttle on resources.graphql.remaining, not on core. Add '
      + 'rateLimit { cost remaining } to your real queries so each one reports '
      + 'its own price, and point the health check at the bucket the traffic '
      + 'actually spends.';
  }
  if (state === 'graphql-tight') {
    return 'slow down now rather than at zero. Divide the remaining points by '
      + 'the measured cost of your query to get the number of calls you have '
      + 'left, and space them out.';
  }
  if (state === 'rest-exhausted-graphql-healthy') {
    return 'see /github/rate-limit-core-exhausted/ -- this is the REST hourly '
      + 'quota and the repair for it is conditional requests and webhooks, not '
      + 'point budgeting.';
  }
  if (state === 'both-exhausted') {
    return 'wait for the resets and then fix them separately: they refill on '
      + 'their own schedules and neither repair helps the other.';
  }
  if (state === 'both-healthy') {
    return 'nothing today. Measure the cost of your query anyway, because the '
      + 'budget in queries is what you schedule against and it is not 5,000.';
  }
  return 'read GET /rate_limit with a token this API accepts.';
}

/** Points this run will spend. Pure. Zero unless the in-band probe is asked for. */
export function pointCost(inBand) {
  return inBand ? 1 : 0;
}

/** A reset delay in something readable. Pure. */
export function fmtReset(seconds) {
  if (seconds === null || seconds === undefined) return 'unknown';
  const n = Number(seconds);
  if (!Number.isFinite(n)) return 'unknown';
  return n < 90 ? `${Math.trunc(n)}s` : `${Math.round(n / 60)}m`;
}

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'Content-Type': 'application/json',
    'User-Agent': UA,
  };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN (read-only is enough)');
    process.exitCode = 2;
    return;
  }
  const inBand = Boolean((process.env.GITHUB_IN_BAND || "dummy-github-in-band"));
  const document = (process.env.GITHUB_QUER || "dummy-github-quer")Y || BUDGET_QUERY;
  if (inBand) {
    const whyNot = refusal(document);
    if (whyNot) {
      console.error(`refusing to send: ${whyNot}`);
      process.exitCode = 2;
      return;
    }
    console.log(`point cost: ${pointCost(true)} point(s) against the 5,000/hour GraphQL budget`);
  } else {
    console.log(`point cost: ${pointCost(false)} point(s). GET /rate_limit reports `
      + 'the GraphQL bucket and does not consume any of it.');
  }

  const res = await fetch(`${API}/rate_limit`, { headers: headers(token) });
  if (res.status === 401) {
    console.error('401 from GitHub: GITHUB_TOKEN is missing, malformed or revoked');
    process.exitCode = 2;
    return;
  }
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  const graphqlB = bucket(body, 'graphql');
  const coreB = bucket(body, 'core');
  const now = Math.trunc(Date.now() / 1000);

  for (const [name, b] of [['core', coreB], ['graphql', graphqlB]]) {
    if (!b) { console.log(`${name.padEnd(8)} not reported`); continue; }
    console.log(`${name.padEnd(8)} ${b.remaining} / ${b.limit} remaining, `
      + `resets in ${fmtReset(secondsToReset(b, now))}`);
  }
  if (graphqlB) {
    console.log(`budget: a limit of ${graphqlB.limit} points/hour is ${identifyBudget(graphqlB.limit)}`);
  }

  const [state, detail] = classify(graphqlB, coreB);
  console.log(`${state}: ${detail}`);

  let measured = Number((process.env.GITHUB_COS || "dummy-github-cos")T || 1);
  let envelope = null;
  if (inBand) {
    const probe = await fetch(`${API}/graphql`, {
      // A GraphQL query is a read. POST is only how the document reaches the
      // endpoint, and refusal() has already rejected anything that is not.
      method: 'POST',
      headers: headers(token),
      body: JSON.stringify({ query: document }),
    });
    try { envelope = await probe.json(); } catch { envelope = null; }
    if (isRateLimited(envelope)) {
      console.log('the in-band probe itself came back RATE_LIMITED, which is the '
        + 'finding stated by the endpoint rather than inferred');
    }
    const cost = inBandCost(envelope);
    if (cost !== null) {
      measured = cost;
      console.log(`measured cost: ${cost} point(s) for this query shape`);
    } else {
      console.log('the response carried no rateLimit.cost; add '
        + 'rateLimit { cost remaining } to the document');
    }
  }

  const limit = graphqlB ? graphqlB.limit : null;
  const remaining = graphqlB ? graphqlB.remaining : null;
  const rate = sustainableRate(limit, measured);
  const gap = secondsBetween(limit, measured);
  if (rate) {
    console.log(`at ${measured} points a query the budget is ${rate} queries/hour, one every ${gap}s`);
  }
  const left = queriesLeft(remaining, measured);
  if (left !== null) {
    console.log(`${remaining} point(s) left is ${left} more quer${left === 1 ? 'y' : 'ies'} of this shape`);
  }
  console.log(`repair: ${repair(state)}`);

  console.log(JSON.stringify({
    points_spent: pointCost(inBand),
    graphql: graphqlB,
    core: coreB,
    graphql_used_fraction: usedFraction(graphqlB),
    core_used_fraction: usedFraction(coreB),
    budget_identified_as: identifyBudget(limit),
    measured_cost: measured,
    queries_per_hour: rate,
    seconds_between_queries: gap,
    queries_left: left,
    in_band_rate_limited: isRateLimited(envelope),
    state,
    detail,
  }, null, 2));
  const bad = ['graphql-exhausted-rest-healthy', 'graphql-tight', 'both-exhausted'];
  process.exitCode = bad.includes(state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
