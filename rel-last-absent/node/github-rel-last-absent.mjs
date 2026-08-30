/**
 * Sort GitHub list endpoints into the ones you can index and the ones you can
 * only walk.
 *
 * Read only. One GET per probed path at per_page=1, which is the cheapest
 * request that still produces a Link header. No items are read, nothing is
 * written, and the repair is printed rather than performed.
 *
 * Environment:
 *   GITHUB_TOKEN  a token with read access to the repository
 *   GITHUB_REPO   owner/name
 */
const API = 'https://api.github.com';
const UA = 'github-rel-last-absent/1.0';

const LINK = /<([^>]+)>\s*;\s*rel="([^"]+)"/g;

const PROBES = ['issues', 'pulls', 'branches', 'events', 'commits'];

/** What each shape of header actually supports. Data, not a chain of ifs. */
export const CAPABILITIES = {
  indexable: {
    walk: true, page_count: true, progress_bar: true, parallel_fanout: true, jump_to_last: true,
  },
  'walk-only': {
    walk: true, page_count: false, progress_bar: false, parallel_fanout: false, jump_to_last: false,
  },
  'single-page': {
    walk: true, page_count: true, progress_bar: true, parallel_fanout: false, jump_to_last: false,
  },
};

const PATTERN_NAMES = {
  page_count: 'page count',
  progress_bar: 'progress bar',
  parallel_fanout: 'parallel fan-out',
  jump_to_last: 'jump to last',
};

/** Parse a Link header into {rel: url}. Pure. */
export function parseLink(header) {
  const out = {};
  if (!header) return out;
  for (const m of String(header).matchAll(LINK)) out[m[2]] = m[1];
  return out;
}

/** The rel names present, sorted. Pure. */
export function rels(links) {
  return Object.keys(links || {}).sort();
}

/** The page query parameter on a pagination URL, or null. Pure. */
export function pageParam(url) {
  if (!url) return null;
  try {
    const raw = new URL(url, API).searchParams.get('page');
    const n = Number(raw);
    return Number.isFinite(n) && raw !== null ? Math.trunc(n) : null;
  } catch {
    return null;
  }
}

/** One of indexable, walk-only, single-page. Pure. */
export function paginationStyle(links) {
  const l = links || {};
  if (Object.prototype.hasOwnProperty.call(l, 'last')) return 'indexable';
  if (Object.prototype.hasOwnProperty.call(l, 'next')) return 'walk-only';
  return 'single-page';
}

/** The number of pages, or null where it cannot be known. Pure. */
export function pageCount(links) {
  if (paginationStyle(links) === 'single-page') return 1;
  return pageParam((links || {}).last);
}

/** The page count the careless way: a missing value becomes 1. Pure. */
export function naivePageCount(links) {
  return pageCount(links) || 1;
}

/** Total items, but only where the endpoint can be indexed. Pure. */
export function itemCount(links, perPage) {
  const pages = pageCount(links);
  const size = Number(perPage);
  if (pages === null || !Number.isFinite(size) || size !== 1) return null;
  return pages;
}

/** What a pager may rely on against an endpoint of this shape. Pure. */
export function capabilities(style) {
  return { ...(CAPABILITIES[style] || CAPABILITIES['walk-only']) };
}

/** The named patterns that do not work here, in a fixed order. Pure. */
export function unavailable(style) {
  const caps = capabilities(style);
  return ['page_count', 'progress_bar', 'parallel_fanout', 'jump_to_last']
    .filter((k) => !caps[k]).map((k) => PATTERN_NAMES[k]);
}

/** Classify one endpoint's header. Pure. Returns [state, detail]. */
export function verdict(links, perPage = 1) {
  const style = paginationStyle(links);
  if (style === 'walk-only') {
    return [style,
      'rel="next" is present and rel="last" is not, so the size of this list is '
      + 'only knowable by walking it. A careful page count says unknown here; '
      + `code that defaults a missing count to 1 reports ${naivePageCount(links)} page.`];
  }
  if (style === 'indexable') {
    const total = itemCount(links, perPage);
    return [style,
      `rel="last" is present, so this endpoint can be indexed: ${pageCount(links)} `
      + `page(s) at per_page=${perPage}${total ? `, which is ${total} item(s)` : ''}. `
      + 'That number is computed per request and moves between calls, so it is a '
      + 'display value rather than a bound.'];
  }
  return [style,
    'neither rel="next" nor rel="last" is present. One request is the whole list '
    + 'here, and nothing about paging applies.'];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (state === 'walk-only') {
    return 'terminate on the absence of rel="next" and never require rel="last". '
      + 'Drop the progress bar or make it indeterminate, and replace any fan-out '
      + 'over a page range with a sequential walk that follows the next URL '
      + 'exactly as given.';
  }
  if (state === 'indexable') {
    return 'nothing, provided rel="last" is treated as a snapshot. Do not cache '
      + 'it as the size of the job, and do not let its absence on some other '
      + 'endpoint default to 1.';
  }
  return 'nothing.';
}

/** Requests this run will spend against the core quota. Pure. */
export function readCost(paths) {
  return Array.isArray(paths) ? paths.length : 0;
}

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const repo = (process.env.GITHUB_REPO || "dummy-github-repo");
  if (!token || !repo) {
    console.error('set GITHUB_TOKEN (read-only is enough) and GITHUB_REPO=owner/name');
    process.exitCode = 2;
    return;
  }
  const paths = PROBES.map((name) => `/repos/${repo}/${name}`);
  console.log(`read cost: ${readCost(paths)} request(s) against the core hourly quota`);

  const findings = [];
  for (const path of paths) {
    const url = new URL(API + path);
    url.searchParams.set('per_page', '1');
    const res = await fetch(url, { headers: headers(token) });
    if (res.status !== 200) {
      console.log(`${path} returned ${res.status}; skipping it`);
      continue;
    }
    const links = parseLink(res.headers.get('link'));
    const [state, detail] = verdict(links, 1);
    console.log(`${path}: rels ${rels(links).join(', ') || 'none'} -> ${state}`);
    console.log(`${state}: ${detail}`);
    const missing = unavailable(state);
    if (missing.length) console.log(`unavailable here: ${missing.join(', ')}`);
    console.log(`repair: ${repair(state)}`);
    findings.push({
      path,
      rels: rels(links),
      style: state,
      pages: pageCount(links),
      pages_if_missing_defaults_to_one: naivePageCount(links),
      items: itemCount(links, 1),
      capabilities: capabilities(state),
      unavailable: missing,
      detail,
    });
  }

  console.log(JSON.stringify({ requests_spent: readCost(paths), findings }, null, 2));
  process.exitCode = findings.some((f) => f.style === 'walk-only') ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
