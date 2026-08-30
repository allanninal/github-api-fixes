/**
 * Report search queries whose results cannot be paged through in full.
 *
 * Read only. GET requests and nothing else: a token with read access is enough.
 * The repair is printed, never performed.
 */
const API = 'https://api.github.com';

const CAP = 1000;
const NEAR = 900;
const MAX_PER_PAGE = 100;

/**
 * The highest page number that lies entirely inside the 1,000-result cap. Pure.
 * The request that straddles the boundary is the one that returns 422.
 */
export function lastReachablePage(perPage = MAX_PER_PAGE) {
  const size = Math.min(Math.max(Number(perPage) || 30, 1), MAX_PER_PAGE);
  return Math.floor(CAP / size);
}

/** Classify one query against the cap. Pure. Returns [state, detail]. */
export function reach(totalCount, perPage = MAX_PER_PAGE) {
  const total = Number(totalCount) || 0;
  const size = Math.min(Math.max(Number(perPage) || 30, 1), MAX_PER_PAGE);
  const last = lastReachablePage(perPage);

  if (total <= 0) return ['no-matches', 'no results; the query matches nothing'];

  if (total > CAP) {
    const slices = Math.ceil(total / CAP);
    return ['capped',
      `total_count is ${total} and only the first ${CAP} are reachable, so ` +
      `${total - CAP} match(es) cannot be paged to at any page size. Page ` +
      `${last} at per_page=${perPage} is the last that works; the next one ` +
      `returns 422. Partition into at least ${slices} narrower queries.`];
  }

  if (total >= NEAR) {
    return ['near-cap',
      `total_count is ${total}, inside the 1,000-result cap but close to it. ` +
      `This query starts losing results silently as soon as it grows past ` +
      `${CAP}; partition it now rather than after.`];
  }

  return ['reachable',
    `total_count is ${total}, all reachable in ${Math.ceil(total / size)} ` +
    `request(s) at per_page=${perPage}.`];
}

function args(name) {
  const out = [];
  process.argv.forEach((a, i) => { if (a === `--${name}`) out.push(process.argv[i + 1]); });
  return out;
}

async function get(token, path, params = {}) {
  const url = new URL(API + path);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  const res = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'github-search-cap-audit',
    },
  });
  if (res.status === 401) {
    throw new Error('401 from GitHub: GITHUB_TOKEN is missing, malformed or revoked');
  }
  if (res.status === 403) {
    throw new Error('403 from GitHub. Search has its own small per-minute bucket; ' +
                    'GET /rate_limit reports resources.search and does not itself ' +
                    'consume quota');
  }
  if (res.status === 422) {
    throw new Error('422 from search: the query is malformed, or it already ' +
                    'reaches past the 1,000-result cap');
  }
  if (!res.ok) throw new Error(`${res.status} from ${url.pathname}`);
  return res.json();
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const queries = args('query');
  if (!token || queries.length === 0) {
    console.error('set GITHUB_TOKEN and pass --query "..." at least once');
    process.exitCode = 2;
    return;
  }
  const endpoint = args('endpoint')[0] ?? 'issues';
  const perPage = Number(args('per-page')[0] ?? MAX_PER_PAGE) || MAX_PER_PAGE;

  // Free to ask: /rate_limit is not billed against any bucket, and search is not
  // billed against core.
  const quota = (await get(token, '/rate_limit')).resources?.search ?? {};
  console.log(`search bucket: ${quota.remaining ?? '?'} of ${quota.limit ?? '?'} ` +
              `remaining, resets at ${quota.reset ?? '?'}`);

  let over = 0;
  for (const q of queries) {
    const body = await get(token, `/search/${endpoint}`, { q, per_page: 1 });
    const [state, detail] = reach(body.total_count, perPage);
    const line = `${state.padEnd(10)} ${q}  ${detail}`;
    if (state === 'capped' || state === 'near-cap') {
      over += 1;
      console.warn(line);
      console.warn('  repair: split this query by created: date ranges, by repo:, ' +
                   'or by label until every slice reports under 1,000, then union ' +
                   'the slices and de-duplicate on id. For a full inventory use ' +
                   'the matching list endpoint instead, which has no such cap.');
    } else {
      console.log(line);
    }
  }

  console.log(`${queries.length} quer(y/ies), ${over} over or near the ${CAP}-result cap`);
  process.exitCode = over ? 1 : 0;
}

// Only run when invoked directly, so the test file can import the pure functions
// without main() running, failing on the missing token and failing the suite.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
