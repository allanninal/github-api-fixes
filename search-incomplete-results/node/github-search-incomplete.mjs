/**
 * Say whether a GitHub search is being answered in part and nobody noticed.
 *
 * Read only. One GET per round against /search/*, three rounds by default,
 * with a pause between them. Nothing is written.
 *
 * Search runs against a server-side timeout. When a query outruns it, GitHub
 * returns what it found with incomplete_results set to true rather than
 * failing. This is not the 1,000-result ceiling: total_count is read here
 * purely so that ceiling can be ruled out by name.
 *
 * Environment:
 *   GITHUB_TOKEN   any token with read access
 *   GITHUB_QUERY   the search query, as sent
 *   GITHUB_KIND    issues, repositories, code, users, commits. Default issues
 *   GITHUB_ROUNDS  how many times to run the same query. Default 3
 */
const API = 'https://api.github.com';
const UA = 'github-search-incomplete/1.0';

/** The other Search limit, read only so it can be excluded as an explanation. */
export const RESULT_CAP = 1000;
/** Authenticated search requests per minute. The check is sized against this. */
export const SEARCH_BUCKET = 30;

const QUALIFIER = /(?:^|\s)-?([A-Za-z_]+):\S/g;

export const SCOPES = ['repo', 'org', 'user'];
export const RANGES = ['created', 'updated', 'merged', 'closed'];

/** Whether this response says it is partial. Pure. */
export function flagged(body) {
  return Boolean(body && typeof body === 'object' && body.incomplete_results === true);
}

/** The reported match count, or null. Pure. */
export function totalOf(body) {
  if (!body || typeof body !== 'object') return null;
  const raw = body.total_count;
  if (raw === null || raw === undefined || raw === '') return null;
  const n = Number(raw);
  return Number.isFinite(n) ? Math.trunc(n) : null;
}

/** How many items were actually delivered. Pure. */
export function itemCount(body) {
  if (!body || typeof body !== 'object') return 0;
  return Array.isArray(body.items) ? body.items.length : 0;
}

/** Whether this response may be stored. Pure. */
export function cacheable(body) {
  return Boolean(body && typeof body === 'object') && !flagged(body);
}

/** Whether the ceiling could also be in play here. Pure. */
export function aboveResultCap(total) {
  const n = Number(total);
  return Number.isFinite(n) && n > RESULT_CAP;
}

/** The qualifier names used in a search query. Pure. */
export function qualifiers(query) {
  const out = new Set();
  for (const m of ` ${String(query ?? '')}`.matchAll(QUALIFIER)) out.add(m[1]);
  return out;
}

/** Which narrowing devices the query is not already using. Pure. */
export function narrowing(query) {
  const have = qualifiers(query);
  const out = [];
  if (!SCOPES.some((s) => have.has(s))) out.push('repo: or org:');
  if (!RANGES.some((s) => have.has(s))) out.push('created: or updated: date range');
  if (!have.has('language')) out.push('language:');
  return out;
}

/** The three fields worth keeping from one response. Pure. */
export function observe(body) {
  return { incomplete: flagged(body), total: totalOf(body), items: itemCount(body) };
}

/** Counts over the sequence of rounds. Pure. */
export function summarise(observations) {
  const obs = Array.isArray(observations) ? observations : [];
  return {
    rounds: obs.length,
    flagged: obs.filter((o) => o && o.incomplete).length,
    item_counts: obs.map((o) => (o || {}).items),
    totals: obs.map((o) => (o || {}).total),
  };
}

/** Whether identical queries returned identical item counts. Pure. */
export function countsStable(observations) {
  const counts = (Array.isArray(observations) ? observations : []).map((o) => (o || {}).items);
  return new Set(counts).size <= 1;
}

/** The largest reported match count across the rounds, or null. Pure. */
export function maxTotal(observations) {
  const totals = (Array.isArray(observations) ? observations : [])
    .map((o) => (o || {}).total).filter((t) => t !== null && t !== undefined);
  return totals.length ? Math.max(...totals) : null;
}

/** Classify the sequence. Pure. Returns [state, detail]. */
export function verdict(observations) {
  const s = summarise(observations);
  if (!s.rounds) return ['no-observations', 'no round completed, so there is nothing to judge.'];
  const top = maxTotal(observations);
  const ceiling = top !== null && !aboveResultCap(top)
    ? ` total_count is ${top}, well inside the ${RESULT_CAP}-result ceiling, so the ceiling is not the explanation.`
    : '';
  if (s.flagged && top !== null && aboveResultCap(top)) {
    return ['timed-out-and-capped',
      `${s.flagged} of ${s.rounds} round(s) came back partial and total_count is `
      + `${top}, which is also above the ${RESULT_CAP}-result ceiling. These are `
      + 'two separate problems that look alike from outside and need repairing separately.'];
  }
  if (s.flagged === s.rounds) {
    return ['timed-out-always',
      `every one of ${s.rounds} round(s) came back partial, so this query does not `
      + `finish inside the search timeout. No retry policy will fix that.${ceiling}`];
  }
  if (s.flagged) {
    return ['timed-out-intermittent',
      `${s.flagged} of ${s.rounds} round(s) came back partial, so the query `
      + 'sometimes finishes and sometimes does not. A flagged response is a '
      + `retry, not a result.${ceiling}`];
  }
  if (!countsStable(observations)) {
    const seen = [...new Set(s.item_counts)].sort((a, b) => a - b).join(' and ');
    return ['unstable-counts',
      `no round was flagged, but identical queries returned ${seen} item(s) across `
      + 'the rounds. Something is truncating or reordering underneath you, and the '
      + 'answer should be treated the same way as a flagged one.'];
  }
  return ['complete',
    `${s.rounds} of ${s.rounds} round(s) were unflagged and the item count did not move.`];
}

/** What actually helps: retry, narrow, or nothing. Pure. */
export function retryOrNarrow(observations) {
  const state = verdict(observations)[0];
  if (['timed-out-always', 'timed-out-and-capped'].includes(state)) return 'narrow';
  if (['timed-out-intermittent', 'unstable-counts'].includes(state)) return 'retry';
  return 'nothing';
}

/** The sentence a reader has to act on. Pure. */
export function repair(state, query = '') {
  const missing = narrowing(query).join(', ') || 'nothing obvious';
  if (state === 'timed-out-always') {
    return `narrow the query until it finishes: add ${missing}. Retrying will `
      + 'spend your search bucket on the same partial answer.';
  }
  if (state === 'timed-out-and-capped') {
    return 'narrow the query until it finishes and until each slice reports under '
      + `${RESULT_CAP} matches: add ${missing}, then union the slices yourself.`;
  }
  if (state === 'timed-out-intermittent') {
    return 'treat a flagged response as a retry, never as a result, and never '
      + `cache it. If the flag keeps coming back, add ${missing}.`;
  }
  if (state === 'unstable-counts') {
    return 'treat this the same as a flagged response: do not cache it and do not '
      + 'diff against it. A moving count with no flag is still a moving count.';
  }
  return 'nothing.';
}

/** Search requests this run will spend. Pure. */
export function readCost(queries, rounds) {
  const q = Array.isArray(queries) ? queries.length : 0;
  const r = Number(rounds);
  return q * (Number.isFinite(r) && r > 0 ? Math.trunc(r) : 0);
}

/** Whether a plan of this size fits the per-minute search allowance. Pure. */
export function withinSearchBucket(cost) {
  const n = Number(cost);
  return Number.isFinite(n) && n > 0 && n <= SEARCH_BUCKET;
}

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
}

const wait = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const query = (process.env.GITHUB_QUERY || "dummy-github-query");
  if (!token || !query) {
    console.error('set GITHUB_TOKEN (read-only is enough) and GITHUB_QUERY');
    process.exitCode = 2;
    return;
  }
  const kind = (process.env.GITHUB_KIN || "dummy-github-kin")D || 'issues';
  const rounds = Number((process.env.GITHUB_ROUND || "dummy-github-round")S || 3);
  const cost = readCost([query], rounds);
  if (!withinSearchBucket(cost)) {
    console.error(`${cost} request(s) does not fit the ${SEARCH_BUCKET} per minute search bucket`);
    process.exitCode = 2;
    return;
  }
  console.log(`read cost: ${cost} search request(s) of the ${SEARCH_BUCKET} per minute search bucket`);

  const observations = [];
  for (let n = 0; n < rounds; n += 1) {
    if (n) await wait(2000);
    const url = new URL(`${API}/search/${kind}`);
    url.searchParams.set('q', query);
    url.searchParams.set('per_page', '100');
    const res = await fetch(url, { headers: headers(token) });
    if (res.status !== 200) {
      console.error(`search returned ${res.status}`);
      continue;
    }
    let body = null;
    try { body = await res.json(); } catch { body = null; }
    if (body === null) continue;
    const o = observe(body);
    observations.push(o);
    console.log(`round ${n + 1}: ${o.items} item(s), total_count ${o.total}, `
      + `incomplete_results=${o.incomplete}`);
    if (!cacheable(body)) console.log(`round ${n + 1} must not be cached or diffed against`);
  }

  const [state, detail] = verdict(observations);
  console.log(`${state}: ${detail}`);
  const missing = narrowing(query);
  if (missing.length) console.log(`missing from the query: ${missing.join(', ')}`);
  console.log(`what helps: ${retryOrNarrow(observations)}`);
  console.log(`repair: ${repair(state, query)}`);

  console.log(JSON.stringify({
    query,
    requests_spent: observations.length,
    summary: summarise(observations),
    counts_stable: countsStable(observations),
    total_above_cap: aboveResultCap(maxTotal(observations)),
    qualifiers_used: [...qualifiers(query)].sort(),
    narrowing_available: missing,
    action: retryOrNarrow(observations),
    state,
    detail,
  }, null, 2));
  process.exitCode = ['complete', 'no-observations'].includes(state) ? 0 : 1;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
