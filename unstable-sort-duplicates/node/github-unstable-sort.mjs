/**
 * Show that a paginated walk is being reordered underneath itself.
 *
 * Read only. One GET per page per walk, two walks. Nothing is written and the
 * repair is printed rather than performed.
 *
 * Environment:
 *   GITHUB_TOKEN      a token with read access to the repository
 *   GITHUB_REPO       owner/name
 *   GITHUB_PATH       API path to walk, default the repo's issues
 *   GITHUB_SORT       the sort your request actually sends, e.g. updated
 *   GITHUB_DIRECTION  asc or desc, default desc
 *   GITHUB_PAGES      pages per walk, default 3
 */
const API = 'https://api.github.com';
const UA = 'github-unstable-sort/1.0';

export const MAX_PER_PAGE = 100;

/** Keys whose value changes while you are reading the collection. */
export const MUTABLE_SORTS = new Set(['updated', 'pushed', 'comments', 'popularity',
  'long-running', 'reactions', 'interactions', 'best-match', 'relevance', 'stars',
  'forks', 'help-wanted-issues']);

/** Keys that are set once and never move afterwards. */
export const IMMUTABLE_SORTS = new Set(['created', 'full_name', 'id']);

/** What GitHub applies when a request names a sort but no direction. */
export const DEFAULT_DIRECTION = 'desc';

const LINK = /<([^>]+)>\s*;\s*rel="([^"]+)"/g;

/** Parse a Link header into {rel: url}. Pure. */
export function parseLink(header) {
  const out = {};
  if (!header) return out;
  for (const m of String(header).matchAll(LINK)) out[m[2]] = m[1];
  return out;
}

/** Ids as strings, so two walks compare and sort the same way. Pure. */
export function normalize(ids) {
  return (ids || []).map((i) => String(i));
}

/** Whether this sort key moves while you read. Pure. */
export function sortKind(sort) {
  const key = String(sort ?? '').trim().toLowerCase();
  if (MUTABLE_SORTS.has(key)) return 'mutable';
  if (IMMUTABLE_SORTS.has(key)) return 'immutable';
  return 'unknown';
}

/** What a walk over this ordering can lose. Pure. Returns [risk, detail]. */
export function walkRisk(sort, direction = null) {
  const kind = sortKind(sort);
  const way = String(direction ?? DEFAULT_DIRECTION).trim().toLowerCase();
  if (kind === 'unknown') {
    return ['unknown',
      `${sort} is not a sort key this check knows, so name the one your request `
      + 'actually sends.'];
  }
  if (way !== 'asc' && way !== 'desc') {
    return ['unknown', `${direction} is not a direction.`];
  }
  if (kind === 'mutable') {
    return ['skips-and-duplicates',
      `sort=${sort} is a key that changes while you read, so a row can move `
      + 'anywhere between two requests. Both skips and duplicates are possible '
      + 'and only one of them is visible.'];
  }
  if (way === 'desc') {
    return ['duplicates-only',
      `sort=${sort} descending is stable per row, but new rows arrive at the `
      + 'head and shift your window, so a record can be returned twice. Nothing '
      + 'can be hidden.'];
  }
  return ['append-only',
    `sort=${sort} ascending only grows at the end you have not reached yet, so `
    + 'this walk can neither skip a record nor return one twice.'];
}

/** Ids returned more than once inside a single walk. Pure, sorted. */
export function duplicatesWithin(ids) {
  const seen = new Set();
  const twice = new Set();
  for (const i of normalize(ids)) {
    if (seen.has(i)) twice.add(i);
    seen.add(i);
  }
  return [...twice].sort();
}

/** Diff two walks of the same window. Pure. */
export function compareWalks(first, second) {
  const a = normalize(first);
  const b = normalize(second);
  const sa = new Set(a);
  const sb = new Set(b);
  return {
    missing: [...sa].filter((i) => !sb.has(i)).sort(),
    appeared: [...sb].filter((i) => !sa.has(i)).sort(),
    repeated: [...new Set([...duplicatesWithin(a), ...duplicatesWithin(b)])].sort(),
    first_count: a.length,
    second_count: b.length,
  };
}

/** Which parts of a two-walk diff actually prove instability. Pure. */
export function evidence(risk, diff) {
  const d = diff || {};
  if (risk === 'skips-and-duplicates') {
    return [...new Set([...(d.missing || []), ...(d.appeared || [])])].sort();
  }
  if (risk === 'append-only') return [...(d.missing || [])].sort();
  return [];
}

/** Classify the ordering, and the evidence if there is any. Pure. */
export function verdict(sort, direction = null, first = null, second = null) {
  const [risk, detail] = walkRisk(sort, direction);
  if (risk === 'unknown') return ['unknown', detail];

  if (first !== null && second !== null) {
    const diff = compareWalks(first, second);
    const proof = evidence(risk, diff);
    if (proof.length) {
      return ['proven-skips',
        `${proof.length} id(s) appeared in one walk of this window and not the `
        + 'other, so the ordering moved between the two reads and a record on a '
        + 'page boundary was never returned.'];
    }
    if (diff.repeated.length) {
      return ['proven-duplicates',
        `${diff.repeated.length} id(s) came back twice inside a single walk, so `
        + 'the window shifted mid read. Nothing was hidden, but the job '
        + 'processed a record more than once.'];
    }
  }

  if (risk === 'skips-and-duplicates') {
    return ['exposed',
      `${detail} The two walks agreed this time, which is a quiet window rather `
      + 'than a safe walk.'];
  }
  if (risk === 'duplicates-only') return ['insertion-shift', detail];
  return ['stable-walk', detail];
}

/** The request that makes the walk safe. Pure. */
export function stableParams(perPage = MAX_PER_PAGE, since = null) {
  const params = { sort: 'created', direction: 'asc', per_page: Number(perPage) };
  if (since) params.since = since;
  return params;
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (state === 'proven-skips' || state === 'exposed') {
    return 'sort on an immutable key ascending, sort=created&direction=asc, so '
      + 'the collection only grows at the end you have not reached. For '
      + 'incremental work add since=<timestamp> and deduplicate on id, and for '
      + 'long walks use GraphQL cursors instead of offsets.';
  }
  if (state === 'proven-duplicates' || state === 'insertion-shift') {
    return 'deduplicate on id as you go, and prefer direction=asc so new rows '
      + 'land behind your read position instead of in front of it. Nothing is '
      + 'being lost here, but the same record is being processed more than once.';
  }
  if (state === 'stable-walk') {
    return 'nothing on the ordering. Keep per_page at 100 to reduce the number '
      + 'of seams, and keep the sort where it is.';
  }
  return 'name the sort and direction your request actually sends.';
}

/** Requests this run will spend against the core quota. Pure. */
export function readCost(pages, walks = 2) {
  const p = Number(pages);
  const w = Number(walks);
  if (!Number.isFinite(p) || !Number.isFinite(w)) return 0;
  return Math.max(0, Math.trunc(p)) * Math.max(0, Math.trunc(w));
}

function headersFor(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
}

async function walkOnce(token, path, params, pages) {
  const ids = [];
  const url = new URL(API + path);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, String(v));
  let next = url.toString();
  for (let i = 0; i < Math.max(1, pages) && next; i += 1) {
    const res = await fetch(next, { headers: headersFor(token) });
    if (!res.ok) {
      console.log(`${next} returned ${res.status}; stopping this walk`);
      break;
    }
    let items = null;
    try { items = await res.json(); } catch { items = null; }
    if (!Array.isArray(items)) break;
    for (const item of items) if (item && item.id !== undefined) ids.push(item.id);
    next = parseLink(res.headers.get('link')).next || null;
  }
  return normalize(ids);
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const repo = (process.env.GITHUB_REPO || "dummy-github-repo");
  const sort = (process.env.GITHUB_SORT || "dummy-github-sort");
  if (!token || !repo || !sort) {
    console.error('set GITHUB_TOKEN (read-only is enough), GITHUB_REPO=owner/name '
      + 'and GITHUB_SORT=updated');
    process.exitCode = 2;
    return;
  }
  const direction = (process.env.GITHUB_DIRECTIO || "dummy-github-directio")N || DEFAULT_DIRECTION;
  const pages = Number((process.env.GITHUB_PAGE || "dummy-github-page")S || 3);
  const path = (process.env.GITHUB_PAT || "dummy-github-pat")H || `/repos/${repo}/issues`;
  console.log(`read cost: ${readCost(pages, 2)} request(s) against the core hourly quota`);

  const [risk, detail] = walkRisk(sort, direction);
  console.log(`sort=${sort} direction=${direction}: ${detail}`);

  const params = { sort, direction, per_page: MAX_PER_PAGE };
  const first = await walkOnce(token, path, params, pages);
  const second = await walkOnce(token, path, params, pages);
  console.log(`walk 1 collected ${first.length} id(s), walk 2 collected ${second.length} id(s)`);

  const diff = compareWalks(first, second);
  const [state, verdictDetail] = verdict(sort, direction, first, second);
  console.log(`${state}: ${verdictDetail}`);
  console.log(`repair: ${repair(state)}`);

  console.log(JSON.stringify({
    requests_spent: readCost(pages, 2),
    path,
    sort,
    direction,
    sort_kind: sortKind(sort),
    risk,
    diff,
    evidence: evidence(risk, diff),
    state,
    detail: verdictDetail,
    stable_params: stableParams(MAX_PER_PAGE),
    repair: repair(state),
  }, null, 2));
  process.exitCode = ['proven-skips', 'proven-duplicates', 'exposed'].includes(state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
