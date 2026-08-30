/**
 * Report how many requests an unset per_page is costing on each list endpoint.
 *
 * Read only. GET requests and nothing else: a token with read access is enough.
 * The repair is printed, never performed.
 *
 * A cost check, not a correctness one. Raising per_page does not make a client
 * that ignores the Link header correct.
 */
const API = 'https://api.github.com';
const LINK = /<([^>]+)>\s*;\s*rel="([^"]+)"/g;

const MAX_PER_PAGE = 100;
const DEFAULT_PER_PAGE = 30;

const PROBES = [
  ['issues', { state: 'all' }],
  ['pulls', { state: 'all' }],
  ['commits', {}],
  ['branches', {}],
  ['tags', {}],
];

/**
 * Requests needed to read `items` at `perPage`. Pure. Clamps at 100 the way the
 * API does, because per_page above the maximum is reduced rather than rejected.
 */
export function pagesFor(items, perPage) {
  const size = Math.min(Math.max(Number(perPage) || DEFAULT_PER_PAGE, 1), MAX_PER_PAGE);
  const n = Number(items) || 0;
  return n <= 0 ? 0 : Math.ceil(n / size);
}

/** Classify one endpoint's page-size arithmetic. Pure. Returns [state, detail]. */
export function verdict(items, perPage = DEFAULT_PER_PAGE) {
  const n = Number(items) || 0;
  if (n <= 0) return ['empty', 'no items; nothing to page and nothing to save'];

  const now = pagesFor(n, perPage);
  const best = pagesFor(n, MAX_PER_PAGE);

  if (now === best) {
    if ((Number(perPage) || DEFAULT_PER_PAGE) > MAX_PER_PAGE) {
      return ['at-maximum',
        `${n} item(s) in ${now} request(s). per_page=${perPage} is above the ` +
        'maximum and was clamped to 100, which costs nothing here but will ' +
        'mislead any loop that trusts the number it asked for.'];
    }
    return [now > 1 ? 'at-maximum' : 'single-page',
      `${n} item(s) in ${now} request(s); per_page=100 would not improve on it.`];
  }

  const saved = now - best;
  const pct = Math.round((100 * saved) / now);
  return ['wasteful',
    `${n} item(s): ${now} request(s) at per_page=${perPage}, ${best} at ` +
    `per_page=100. ${saved} request(s) of quota and ${saved} round trip(s) ` +
    `wasted on every full pass (${pct}%).`];
}

function parseLink(header) {
  const out = new Map();
  if (!header) return out;
  for (const m of String(header).matchAll(LINK)) out.set(m[2], m[1]);
  return out;
}

function pageNumber(url) {
  if (!url) return null;
  const value = new URL(url, API).searchParams.get('page');
  const n = Number(value);
  return value !== null && Number.isInteger(n) ? n : null;
}

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i === -1 ? fallback : process.argv[i + 1];
}

async function get(token, path, params = {}) {
  const url = new URL(API + path);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  const res = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'github-per-page-audit',
    },
  });
  if (res.status === 401) {
    throw new Error('401 from GitHub: GITHUB_TOKEN is missing, malformed or revoked');
  }
  if (!res.ok) throw new Error(`${res.status} from ${url.pathname}`);
  return res;
}

async function countItems(token, path, extra) {
  const first = await get(token, path, { per_page: MAX_PER_PAGE, ...extra });
  const body = await first.json();
  if (!Array.isArray(body)) return null;
  const last = pageNumber(parseLink(first.headers.get('link')).get('last'));
  if (last === null || last <= 1) return body.length;
  const tail = await (await get(token, path,
    { per_page: MAX_PER_PAGE, page: last, ...extra })).json();
  return (last - 1) * MAX_PER_PAGE + tail.length;
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const repo = arg('repo');
  if (!token || !repo) {
    console.error('set GITHUB_TOKEN and pass --repo owner/name');
    process.exitCode = 2;
    return;
  }
  const perPage = Number(arg('per-page', DEFAULT_PER_PAGE)) || DEFAULT_PER_PAGE;

  let wasteful = 0;
  let recoverable = 0;
  for (const [name, extra] of PROBES) {
    const path = `/repos/${repo}/${name}`;
    const items = await countItems(token, path, extra);
    if (items === null) {
      console.log(`skipped      ${path}  not a list endpoint, skipped`);
      continue;
    }
    const [state, detail] = verdict(items, perPage);
    const line = `${state.padEnd(12)} ${path}  ${detail}`;
    if (state === 'wasteful') {
      wasteful += 1;
      recoverable += pagesFor(items, perPage) - pagesFor(items, MAX_PER_PAGE);
      console.warn(line);
      console.warn('  repair: add per_page=100 to this request. It returns the ' +
                   'same data for the same one request per page.');
    } else {
      console.log(line);
    }
  }

  console.log(`${PROBES.length} endpoint(s), ${wasteful} wasteful, ` +
              `${recoverable} request(s) per pass recoverable`);
  process.exitCode = wasteful ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not run main(), fail on the missing token and fail the whole suite.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
