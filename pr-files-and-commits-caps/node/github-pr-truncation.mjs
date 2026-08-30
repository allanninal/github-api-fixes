/**
 * Compare a pull request's own counters against what its lists can return.
 *
 * Read only. Three GETs per pull request. Nothing is written and the repair is
 * printed rather than performed.
 *
 * Environment:
 *   GITHUB_TOKEN     a token with read access to the repository
 *   GITHUB_REPO      owner/name
 *   GITHUB_PRS       comma-separated pull request numbers
 *   GITHUB_PER_PAGE  page size used to probe the lists, default 100
 */
const API = 'https://api.github.com';
const UA = 'github-pr-truncation/1.0';

export const MAX_PER_PAGE = 100;
/** What you get when nobody sets per_page, and where most of the loss happens. */
export const DEFAULT_PER_PAGE = 30;

/** The documented ceilings on the two lists hanging off a pull request. */
export const CAPS = { files: 3000, commits: 250 };

// Anchored on the angle brackets rather than split on commas: a pagination URL
// can carry a comma of its own and splitting on it breaks the link in half.
const LINK = /<([^>]+)>\s*;\s*rel="([^"]+)"/g;
const PAGE = /[?&]page=(\d+)/;

/**
 * A finite number, or null. Pure.
 *
 * Written out because Number(null) is 0 in JavaScript and Number('') is 0 too,
 * which is exactly how a missing counter turns into a confident zero and a
 * diagnostic starts reporting a truncation nobody has.
 */
function toNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** Parse a Link header into {rel: url}. Pure. */
export function parseLink(header) {
  const out = {};
  if (!header) return out;
  for (const m of String(header).matchAll(LINK)) out[m[2]] = m[1];
  return out;
}

/** The page number inside a pagination URL, or null. Pure. */
export function pageOf(url) {
  if (!url) return null;
  const m = PAGE.exec(String(url));
  return m ? Number(m[1]) : null;
}

/** The documented ceiling on this list, or null. Pure. */
export function capFor(kind) {
  return Object.prototype.hasOwnProperty.call(CAPS, kind) ? CAPS[kind] : null;
}

/** Pages required to hold this many items. Pure. null for nonsense input. */
export function pagesNeeded(total, perPage) {
  const n = toNumber(total);
  const size = toNumber(perPage);
  if (n === null || size === null || n < 0 || size < 1) return null;
  return Math.ceil(n / size);
}

/** How many of the declared items the endpoint can actually hand over. Pure. */
export function reachable(kind, declared) {
  const cap = capFor(kind);
  const n = toNumber(declared);
  if (n === null) return null;
  return cap === null ? n : Math.min(n, cap);
}

/** How many items are unreachable through this endpoint. Pure. */
export function beyondCap(kind, declared) {
  const cap = capFor(kind);
  const n = toNumber(declared);
  if (n === null || cap === null) return 0;
  return Math.max(0, n - cap);
}

/**
 * The item count a rel=last page number implies, as [low, high]. Pure.
 *
 * A last page of 3 at per_page=100 means between 201 and 300 items. That band
 * is the widest honest statement the header supports.
 */
export function boundsFromLast(lastPage, perPage) {
  const last = toNumber(lastPage);
  const size = toNumber(perPage);
  if (last === null || size === null || last < 1 || size < 1) return null;
  return [(last - 1) * size + 1, last * size];
}

/** Whether the pull request's own count contradicts the page count. Pure. */
export function counterOutsideBounds(declared, bounds) {
  if (!bounds) return false;
  const n = toNumber(declared);
  if (n === null) return false;
  return n < bounds[0] || n > bounds[1];
}

/** Items a client reading a single page never sees. Pure. */
export function onePageShortfall(declared, perPage = DEFAULT_PER_PAGE) {
  const n = toNumber(declared);
  const size = toNumber(perPage);
  if (n === null || size === null) return 0;
  return Math.max(0, n - Math.max(0, size));
}

/** Classify one list against the counter that describes it. Pure. */
export function verdict(kind, declared, lastPage = null, perPage = MAX_PER_PAGE) {
  const cap = capFor(kind);
  if (cap === null) {
    return ['unknown', `${kind} is not a list this check knows a ceiling for.`];
  }
  const n = toNumber(declared);
  if (n === null) {
    return ['unknown',
      `the pull request did not report a count for ${kind}, so there is nothing `
      + 'to reconcile the list against.'];
  }
  if (n < 0) {
    return ['unknown', `a negative count for ${kind} is not a number this check can use.`];
  }

  const over = beyondCap(kind, n);
  if (over) {
    return ['beyond-cap',
      `${n} ${kind} are declared and the endpoint stops at ${cap}, so ${over} of `
      + 'them cannot be read through it at any page size.'];
  }

  const bounds = boundsFromLast(lastPage, perPage);
  if (counterOutsideBounds(n, bounds)) {
    return ['counter-disagrees',
      `the pull request declares ${n} ${kind} and the Link header stops at page `
      + `${lastPage}, which can hold between ${bounds[0]} and ${bounds[1]}, so `
      + 'the list is shorter than the counter and something truncated it.'];
  }

  if (n > DEFAULT_PER_PAGE) {
    return ['multi-page',
      `${n} ${kind} across ${pagesNeeded(n, perPage) || 1} page(s) at `
      + `per_page=${Number(perPage)}. A client reading one page at the default `
      + `${DEFAULT_PER_PAGE} sees ${Math.min(n, DEFAULT_PER_PAGE)} of them and `
      + `misses ${onePageShortfall(n)}.`];
  }

  return ['single-page',
    `${n} ${kind} fit in one page at any page size, so nothing here is being `
    + 'truncated today.'];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state, kind) {
  if (state === 'beyond-cap' && kind === 'files') {
    return 'request the pull request with the application/vnd.github.diff media '
      + 'type and parse the diff. The JSON list will not return file 3001 '
      + 'however you paginate it.';
  }
  if (state === 'beyond-cap' && kind === 'commits') {
    return 'read the branch through GET /repos/{owner}/{repo}/commits with a sha '
      + 'and a date range, which paginates conventionally and has no ceiling of '
      + 'its own.';
  }
  if (state === 'counter-disagrees') {
    return 'collect the whole list at per_page=100 and compare the count you '
      + 'collected against changed_files and commits on the pull request object, '
      + 'raising rather than logging on a mismatch.';
  }
  if (state === 'multi-page') {
    return 'set per_page=100, follow rel=next to the end, and assert the '
      + 'collected count against the counter on the pull request object. The '
      + 'default page size of 30 is where this is lost.';
  }
  if (state === 'single-page') {
    return 'nothing on this pull request. Run the same check against your '
      + 'largest ones, which are the ones a review bot is trusted on.';
  }
  return 'point the check at a pull request this token can read.';
}

/** Requests this run will spend against the core quota. Pure. */
export function readCost(prs) {
  return 3 * (Array.isArray(prs) ? prs.length : 0);
}

/** The endpoint's own page count, or null when it cannot be known. Pure. */
export function lastPageFrom(links) {
  const last = pageOf((links || {}).last);
  if (last) return last;
  if (links && Object.prototype.hasOwnProperty.call(links, 'next')) return null;
  return 1;
}

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
}

async function get(token, path, params = {}) {
  const url = new URL(API + path);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, String(v));
  const res = await fetch(url, { headers: headers(token) });
  const links = parseLink(res.headers.get('link'));
  if (!res.ok) return { status: res.status, body: null, links };
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return { status: res.status, body, links };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const repo = (process.env.GITHUB_REPO || "dummy-github-repo");
  const prs = ((process.env.GITHUB_PR || "dummy-github-pr")S || '').split(',').map((s) => s.trim()).filter(Boolean);
  if (!token || !repo || prs.length === 0) {
    console.error('set GITHUB_TOKEN (read-only is enough), GITHUB_REPO=owner/name '
      + 'and GITHUB_PRS=4821,4830');
    process.exitCode = 2;
    return;
  }
  const perPage = Number((process.env.GITHUB_PER_PAG || "dummy-github-per-pag")E || MAX_PER_PAGE);
  console.log(`read cost: ${readCost(prs)} request(s) against the core hourly quota`);

  const findings = [];
  for (const number of prs) {
    const base = `/repos/${repo}/pulls/${number}`;
    const { body: pr } = await get(token, base);
    if (!pr || typeof pr !== 'object') continue;
    console.log(`pull ${number} declares ${pr.changed_files} changed file(s) and `
      + `${pr.commits} commit(s)`);

    for (const [kind, declared] of [['files', pr.changed_files], ['commits', pr.commits]]) {
      const { links } = await get(token, `${base}/${kind}`, { per_page: perPage });
      const last = lastPageFrom(links);
      const [state, detail] = verdict(kind, declared, last, perPage);
      console.log(`${kind}: ${state} - ${detail}`);
      console.log(`repair: ${repair(state, kind)}`);
      findings.push({
        pull_request: number,
        list: kind,
        declared,
        cap: capFor(kind),
        reachable: reachable(kind, declared),
        unreachable: beyondCap(kind, declared),
        endpoint_last_page: last,
        implied_bounds: boundsFromLast(last, perPage),
        missed_by_one_default_page: onePageShortfall(declared),
        state,
        detail,
        repair: repair(state, kind),
      });
    }
  }

  console.log(JSON.stringify({ requests_spent: readCost(prs), findings }, null, 2));
  const bad = ['beyond-cap', 'counter-disagrees', 'multi-page'];
  process.exitCode = findings.some((f) => bad.includes(f.state)) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
