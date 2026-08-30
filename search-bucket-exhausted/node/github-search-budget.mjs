/**
 * Budget a search workload against the search bucket, not the core one.
 *
 * Read only. GET /rate_limit is free and reports every bucket; the optional
 * probe issues one real search, which is a GET and costs one search call.
 *
 * Search allows 30 requests a minute over a 60 second window. Core allows
 * 5,000 an hour, which is about 83 a minute. The second comparison is the one
 * that stops people writing a search per item.
 */
const API = 'https://api.github.com';
const UA = 'github-search-budget/1.0';

// A search query is capped at 256 characters and five boolean operators.
export const MAX_QUERY = 256;
export const MAX_OPERATORS = 5;

// The rate-limit document never reports the length of a bucket's window, and
// the windows are not all the same, so a per-minute comparison needs this.
const WINDOWS = {
  core: 3600, graphql: 3600, integration_manifest: 3600,
  code_scanning_upload: 3600, actions_runner_registration: 3600,
  scim: 3600, dependency_sbom: 3600, audit_log: 3600,
  search: 60, code_search: 60, source_import: 60, dependency_snapshots: 60,
};

/**
 * Normalise every bucket to requests per minute. Pure.
 * An unknown window leaves per_minute null rather than guessed: an invented
 * window produces a confident wrong number.
 */
export function bucketPressure(resources, now) {
  const out = {};
  for (const name of Object.keys(resources ?? {}).sort()) {
    const b = resources[name];
    const limit = Number.parseInt(b?.limit, 10);
    const used = Number.parseInt(b?.used ?? 0, 10);
    const reset = Number(b?.reset ?? 0);
    if (!Number.isFinite(limit) || !Number.isFinite(used) || !Number.isFinite(reset)) continue;
    const window = WINDOWS[name] ?? null;
    const remaining = Number.isInteger(b?.remaining) ? b.remaining : Math.max(0, limit - used);
    out[name] = {
      limit, used, remaining, window,
      per_minute: window ? Math.round((limit / (window / 60)) * 10) / 10 : null,
      refills_in: Math.max(0, Math.round(reset - Number(now))),
    };
  }
  return out;
}

/**
 * Cost a one-call-per-item loop against a per-minute allowance. Pure.
 * Calls past the allowance are refused, not queued.
 */
export function planLoop(items, perMinute) {
  const n = Math.max(0, Number.parseInt(items, 10) || 0);
  const rate = Number(perMinute);
  if (!Number.isFinite(rate) || rate <= 0) {
    return { calls: n, minutes: null, refused_in_first_minute: null };
  }
  return {
    calls: n,
    minutes: Math.round((n / rate) * 10) / 10,
    refused_in_first_minute: Math.max(0, n - Math.trunc(rate)),
  };
}

/**
 * Pack repo: qualifiers into as few queries as the length limit allows. Pure.
 * repo: qualifiers are combined as alternatives and do not spend boolean
 * operators, so the binding constraint is the 256 character budget. Greedy is
 * good enough: the qualifiers are all about the same length.
 */
export function packRepoQueries(repos, base = '', maxLen = MAX_QUERY, maxOperators = MAX_OPERATORS) {
  const stem = (base ?? '').trim();
  const operators = stem.split(/\s+/).filter((t) => ['AND', 'OR', 'NOT'].includes(t)).length;

  const queries = [];
  const tooLong = [];
  let current = '';
  for (const repo of repos ?? []) {
    const name = String(repo).trim();
    if (!name) continue;
    const qualifier = `repo:${name}`;
    if (stem.length + 1 + qualifier.length > maxLen) { tooLong.push(name); continue; }
    const candidate = current ? `${current} ${qualifier}` : qualifier;
    if (stem.length + (stem ? 1 : 0) + candidate.length <= maxLen) {
      current = candidate;
    } else {
      queries.push(stem ? `${stem} ${current}` : current);
      current = qualifier;
    }
  }
  if (current) queries.push(stem ? `${stem} ${current}` : current);

  return { queries, too_long: tooLong, operators, over_operator_limit: operators > maxOperators };
}

/** Turn the buckets and the plan into one finding. Pure. */
export function verdict(search, core, plan = null, packed = null) {
  if (!search) {
    return ['no-search-bucket',
      'the rate-limit document did not include a search bucket, so there is ' +
      'nothing to budget against'];
  }

  const coreRate = core?.per_minute ?? null;
  const comparison = coreRate === null ? ''
    : ` Core allows ${Math.round(coreRate)} a minute over its hour, so search ` +
      'is the tighter of the two despite the larger-looking number.';

  if (search.remaining <= 0) {
    return ['exhausted',
      `search is empty and refills in ${search.refills_in} second(s). Core ` +
      `still has ${core?.remaining ?? '?'} of ${core?.limit ?? '?'}, which is ` +
      'why every non-search call kept working: they are different buckets.'];
  }

  if (plan?.refused_in_first_minute) {
    let packing = '';
    if (packed?.queries?.length) {
      packing = ` Packed into repo: qualifiers the same work is ` +
        `${packed.queries.length} quer${packed.queries.length === 1 ? 'y' : 'ies'}.`;
    }
    return ['over-budget',
      `${plan.calls} searches at ${search.per_minute} a minute needs ` +
      `${plan.minutes} minute(s), and ${plan.refused_in_first_minute} of them ` +
      `are refused inside the first minute rather than queued.${packing}${comparison}`];
  }

  if (search.used >= search.limit * 0.8) {
    return ['tight',
      `${search.used} of ${search.limit} spent in the current 60 second ` +
      `window, refilling in ${search.refills_in} second(s).${comparison}`];
  }

  if (plan?.calls) {
    return ['clear',
      `${plan.calls} search(es) at ${search.per_minute} a minute fits in ` +
      `${plan.minutes} minute(s) with nothing refused.${comparison}`];
  }

  return ['clear',
    `${search.remaining} of ${search.limit} left in this window.${comparison}`];
}

async function rateLimit(headers) {
  const res = await fetch(`${API}/rate_limit`, { headers });
  if (res.status !== 200) {
    console.error(`GET /rate_limit returned ${res.status}: ${(await res.text()).slice(0, 200)}`);
    return null;
  }
  return (await res.json()).resources ?? {};
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN (a read-only token is enough)');
    process.exitCode = 2;
    return;
  }
  const repos = (process.argv[2] ?? '').split(',').map((r) => r.trim()).filter(Boolean);
  const base = process.argv[3] ?? 'is:issue is:open';

  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };

  const before = await rateLimit(headers);
  if (!before) { process.exitCode = 2; return; }

  const pressure = bucketPressure(before, Date.now() / 1000);
  for (const [name, b] of Object.entries(pressure)) {
    const rate = b.per_minute ? `${Math.round(b.per_minute)} a minute` : 'window not in this table';
    console.log(`${name.padEnd(28)} ${b.used} / ${b.limit} ${rate}`);
  }

  const search = pressure.search;
  const plan = repos.length ? planLoop(repos.length, search?.per_minute) : null;
  const packed = repos.length ? packRepoQueries(repos, base) : null;

  const [state, detail] = verdict(search, pressure.core, plan, packed);
  console.log(`${state}: ${detail}`);

  if (packed?.queries?.length && state !== 'clear') {
    console.log(`repair: run these ${packed.queries.length} quer` +
      `${packed.queries.length === 1 ? 'y' : 'ies'} instead of one per ` +
      'repository, and filter the combined results client side:');
    for (const q of packed.queries.slice(0, 10)) console.log(`  ${q}`);
    if (packed.queries.length > 10) console.log(`  ... and ${packed.queries.length - 10} more`);
  }
  if (packed?.too_long?.length) {
    console.warn(`${packed.too_long.length} repository name(s) cannot fit in a ` +
      `${MAX_QUERY} character query beside this base query: ` +
      packed.too_long.slice(0, 5).join(', '));
  }
  if (packed?.over_operator_limit) {
    console.warn(`the base query already uses ${packed.operators} boolean ` +
      `operators and the limit is ${MAX_OPERATORS}`);
  }
  if (state !== 'clear') {
    console.log('repair: where a list endpoint can answer the same question, ' +
      'use it instead. Issues, pull requests and commits all have list ' +
      'endpoints billed to core rather than to search.');
    console.log('repair: cache search results by query string. The allowance ' +
      'counts requests, so a repeated query is pure waste.');
  }

  console.log(JSON.stringify({
    state, search, core: pressure.core, plan, queries: packed?.queries ?? [],
  }, null, 2));
  process.exitCode = (state === 'exhausted' || state === 'over-budget') ? 1 : 0;
}

// Only run when invoked directly, so importing this from the test file does not
// start main() and set an exit code the tests never asked for.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
