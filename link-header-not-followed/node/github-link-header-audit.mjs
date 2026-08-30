/**
 * Report GitHub list endpoints that advertise pages your client may not read.
 *
 * Read only. GET requests and nothing else: a token with read access to the
 * repository is enough. The repair is printed, never performed.
 *
 * The API cannot see whether your client follows rel="next". It can only say
 * whether there is a next page there to be missed.
 */
const API = 'https://api.github.com';

// Anchored on the angle brackets rather than split on ','. A pagination URL can
// contain a comma of its own (labels=bug,ci) and splitting the header on commas
// turns one good link into two broken ones.
const LINK = /<([^>]+)>\s*;\s*rel="([^"]+)"/g;

const PROBES = [
  ['pulls', { state: 'open' }],
  ['issues', { state: 'all' }],
  ['branches', {}],
  ['tags', {}],
  ['contributors', {}],
];

/** Parse a Link header into a Map of rel to url. Pure, so it is tested offline. */
export function parseLink(header) {
  const out = new Map();
  if (!header) return out;
  for (const m of String(header).matchAll(LINK)) out.set(m[2], m[1]);
  return out;
}

/** Read the page query parameter out of a pagination URL, or null. */
export function pageNumber(url) {
  if (!url) return null;
  let value;
  try {
    value = new URL(url, API).searchParams.get('page');
  } catch {
    return null;
  }
  const n = Number(value);
  return value !== null && Number.isInteger(n) ? n : null;
}

/**
 * Classify what one list response says about its own completeness. Pure.
 * Returns [state, detail].
 *
 * Three states, not two: a rel="next" with no rel="last" is still a truncated
 * list, and a loop that stops there has the same bug in a different costume.
 */
export function verdict(links, received, perPage = 1) {
  if (!links.has('next')) {
    return ['single-page',
      `${received} item(s) and no rel="next". One request really is the whole ` +
      'list here.'];
  }

  const last = pageNumber(links.get('last'));
  if (last === null) {
    return ['more-pages-unsized',
      'rel="next" is present and rel="last" is not, so the total is only ' +
      'knowable by walking it. Terminate on the absence of rel="next", never ' +
      'on the absence of rel="last".'];
  }

  if (perPage === 1) {
    return ['more-pages',
      `${last} item(s) in total. A client that reads the first page and stops ` +
      `reports ${received}.`];
  }

  return ['more-pages',
    `${last} page(s) at per_page=${perPage}, so ${(last - 1) * perPage + 1} to ` +
    `${last * perPage} item(s) in total. A client that reads the first page and ` +
    `stops reports ${received}.`];
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
      'User-Agent': 'github-link-header-audit',
    },
  });
  if (res.status === 401) {
    throw new Error('401 from GitHub: GITHUB_TOKEN is missing, malformed or revoked');
  }
  if (res.status === 403) {
    throw new Error('403 from GitHub. If this is a rate limit, GET /rate_limit ' +
                    'reports the reset and does not itself consume quota');
  }
  if (res.status === 404) {
    throw new Error(`404 on ${path}: the repository does not exist, or this token ` +
                    'cannot see it');
  }
  if (!res.ok) throw new Error(`${res.status} from ${url.pathname}`);
  return res;
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN (a read-only token is enough)');
    process.exitCode = 2;
    return;
  }
  const repo = arg('repo');
  if (!repo) {
    console.error('usage: node github-link-header-audit.mjs --repo owner/name');
    process.exitCode = 2;
    return;
  }

  const probes = PROBES.map(([name, extra]) => [`/repos/${repo}/${name}`, extra]);

  let truncatable = 0;
  let remaining = '?';
  for (const [path, extra] of probes) {
    const res = await get(token, path, { per_page: 1, ...extra });
    remaining = res.headers.get('x-ratelimit-remaining') ?? '?';
    const body = await res.json();
    const received = Array.isArray(body) ? body.length : 0;
    const [state, detail] = verdict(parseLink(res.headers.get('link')), received, 1);

    const line = `${state.padEnd(18)} ${path}  ${detail}`;
    if (state === 'single-page') { console.log(line); continue; }
    truncatable += 1;
    console.warn(line);
    console.warn('  repair: follow rel="next" until it is absent -- ' +
                 'octokit.paginate() in Octokit, the PaginatedList in PyGithub, ' +
                 'gh api --paginate on the command line. Never build page URLs ' +
                 'by hand.');
  }

  console.log(`${probes.length} endpoint(s) probed, ${truncatable} with pages ` +
              `beyond the first; x-ratelimit-remaining ${remaining}`);
  process.exitCode = truncatable ? 1 : 0;
}

// Only run when invoked directly. The test file imports this module, and without
// the guard main() would run there too, fail on the missing token and set a
// non-zero exit code that fails the whole test file even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
