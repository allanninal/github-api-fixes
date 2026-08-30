/**
 * Cost a code-search scan against the bucket code search is actually billed to.
 *
 * Read only. Every request is a GET. GET /rate_limit consumes no quota, and the
 * optional live probe is a single search with per_page=1.
 *
 * Code search is metered by resources.code_search, roughly 10 a minute. That is
 * a different row from resources.search and from resources.core.
 */
const API = 'https://api.github.com';
const UA = 'github-code-search-budget/1.0';

// Documented defaults, used only to fill in a row GET /rate_limit did not return.
export const DEFAULTS = { code_search: 10, search: 30, core: 5000 };

// A single query cannot return more than this many results, and 100 is the
// largest page the API will serve.
export const RESULT_CAP = 1000;
export const MAX_PAGE = 100;

/**
 * Normalise the resources table from GET /rate_limit. Pure.
 * A missing row comes back with present false and the documented default:
 * "the field is missing" and "the allowance is zero" are different findings.
 */
export function buckets(payload) {
  const resources = (payload ?? {}).resources ?? {};
  const out = {};
  for (const [name, fallback] of Object.entries(DEFAULTS)) {
    const raw = resources[name];
    if (!raw || typeof raw !== 'object') {
      out[name] = { limit: fallback, remaining: null, reset: null, present: false };
      continue;
    }
    const num = (key) => {
      const n = Number.parseInt(raw[key], 10);
      return Number.isFinite(n) ? n : null;
    };
    const limit = num('limit');
    out[name] = {
      limit: limit === null ? fallback : limit,
      remaining: num('remaining'),
      reset: num('reset'),
      present: true,
    };
  }
  return out;
}

/** Requests and wall-clock minutes for a scan that iterates repositories. Pure. */
export function scanCost(repos, queriesPerRepo, perMinute) {
  const r = Math.max(0, Number.parseInt(repos, 10) || 0);
  const q = Math.max(0, Number.parseInt(queriesPerRepo, 10) || 0);
  const rate = Math.max(1, Number.parseInt(perMinute, 10) || 1);
  const needed = r * q;
  return { requests: needed, minutes: needed ? Math.ceil(needed / rate) : 0 };
}

/**
 * Cost of the same coverage as one qualified query per concern, paged. Pure.
 * Capped at RESULT_CAP, because counting pages past it would promise results
 * the API will not serve.
 */
export function collapsedCost(queries, resultsPerQuery, perMinute,
                              pageSize = MAX_PAGE, cap = RESULT_CAP) {
  const q = Math.max(0, Number.parseInt(queries, 10) || 0);
  const results = Math.max(0, Number.parseInt(resultsPerQuery, 10) || 0);
  const size = Math.max(1, Math.min(Number.parseInt(pageSize, 10) || MAX_PAGE, MAX_PAGE));
  const reachable = Math.min(results, cap);
  // A query with no results still costs the one request that discovers that.
  const perQuery = reachable ? Math.ceil(reachable / size) : 1;
  const needed = q * perQuery;
  const rate = Math.max(1, Number.parseInt(perMinute, 10) || 1);
  return {
    requests: needed,
    pages_per_query: perQuery,
    minutes: needed ? Math.ceil(needed / rate) : 0,
    truncated: results > cap,
  };
}

/** Seconds until a bucket resets, floored at zero; null when unreadable. Pure. */
export function secondsUntil(reset, now) {
  const r = Number.parseInt(reset, 10);
  const n = Number.parseInt(now, 10);
  if (!Number.isFinite(r) || !Number.isFinite(n)) return null;
  return Math.max(0, r - n);
}

/** Turn the bucket state and the two costings into a finding. Pure. */
export function verdict(bucket, iterating, collapsed) {
  const limit = bucket.limit || DEFAULTS.code_search;
  const note = bucket.present ? '' :
    ` (GET /rate_limit did not report a code_search row, so this uses the ` +
    `documented default of ${limit} a minute)`;

  if (bucket.remaining === 0) {
    return ['exhausted',
      'the code_search bucket is empty. This is not the core quota, which is ' +
      'why it can read as thousands remaining at the same time. It refills on ' +
      `its own minute-long clock.${note}`];
  }
  if (!iterating.requests) return ['no-scan', `no scan described, so nothing to cost${note}`];

  const ratio = iterating.requests / Math.max(1, collapsed.requests || 1);
  if (ratio >= 4) {
    const queries = Math.max(1, Math.floor((collapsed.requests || 0) /
      Math.max(1, collapsed.pages_per_query || 1)));
    return ['per-repo-scan',
      `${iterating.requests} request(s) is ${iterating.minutes} minute(s) at ` +
      `${limit} a minute; the same coverage as ${queries} qualified quer(y/ies) ` +
      `is ${collapsed.requests} request(s) and ${collapsed.minutes} minute(s). ` +
      `The loop is the cost, not the caching.${note}`];
  }
  if (iterating.minutes > 1) {
    return ['over-budget',
      `${iterating.requests} request(s) at ${limit} a minute is ` +
      `${iterating.minutes} minute(s) of wall clock even if nothing is refused.${note}`];
  }
  return ['clear',
    `${iterating.requests} request(s) fits inside one minute of a ${limit} a ` +
    `minute allowance.${note}`];
}

async function get(token, path, params) {
  const url = new URL(path.startsWith('/') ? API + path : path);
  for (const [k, v] of Object.entries(params ?? {})) url.searchParams.set(k, v);
  const res = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': UA,
    },
  });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  const headers = {};
  for (const [k, v] of res.headers.entries()) headers[k.toLowerCase()] = v;
  return { status: res.status, body, headers };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN. Code search refuses unauthenticated ' +
      'callers outright, so there is no anonymous fallback here');
    process.exitCode = 2;
    return;
  }
  const repos = Number.parseInt(process.argv[2] ?? '0', 10) || 0;
  const queries = Number.parseInt(process.argv[3] ?? '1', 10) || 1;
  const results = Number.parseInt(process.argv[4] ?? '200', 10) || 200;
  const probeQuery = process.argv[5];

  const rate = await get(token, '/rate_limit');
  if (rate.status !== 200) {
    console.error(`GET /rate_limit returned ${rate.status}; cannot read the buckets`);
    process.exitCode = 2;
    return;
  }

  const table = buckets(rate.body);
  const now = Math.floor(Date.now() / 1000);
  for (const name of ['core', 'search', 'code_search']) {
    const row = table[name];
    const wait = secondsUntil(row.reset, now);
    console.log(`${name.padEnd(12)} limit ${row.limit} remaining ` +
      `${row.remaining ?? '?'} reset in ${wait === null ? 'unknown' : `${wait}s`}`);
    if (!row.present) {
      console.warn(`  ${name} was not in the resources table; showing the ` +
        'documented default');
    }
  }

  if (probeQuery) {
    const probe = await get(token, '/search/code', { q: probeQuery, per_page: 1 });
    console.log(`probe: /search/code returned ${probe.status}, billed to ` +
      `${probe.headers['x-ratelimit-resource'] ?? 'an unnamed bucket'}`);
    if (probe.status === 403) {
      console.warn('  a 403 here with core headroom left is this bucket, not ' +
        'the hourly quota and not your token scopes');
    }
  }

  const code = table.code_search;
  const iterating = scanCost(repos, queries, code.limit);
  const collapsed = collapsedCost(Math.max(1, queries), results, code.limit);
  const [state, detail] = verdict(code, iterating, collapsed);
  console.log(`${state}: ${detail}`);

  if (collapsed.truncated) {
    console.warn(`one query cannot return more than ${RESULT_CAP} results, so ` +
      `the collapsed costing counts ${collapsed.pages_per_query} page(s) and ` +
      'stops. Narrow by path, extension or date rather than paging further.');
  }
  if (state === 'per-repo-scan' || state === 'over-budget' || state === 'exhausted') {
    console.log('repair: one qualified query instead of one per repository, ' +
      'for example q=YOURTERM+org:YOURORG with per_page=100, and follow the ' +
      'Link header.');
    console.log('repair: for an exhaustive audit, shallow-clone and grep ' +
      'locally. The search index is capped, ranked and metered; a clone is none ' +
      'of those.');
  }

  console.log(JSON.stringify({ buckets: table, iterating, collapsed, state }, null, 2));
  process.exitCode = (state === 'per-repo-scan' || state === 'over-budget' ||
    state === 'exhausted') ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main(), fail on the missing token and set an exit code that
// fails the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
