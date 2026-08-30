/**
 * Show that per_page above the maximum is reduced rather than refused.
 *
 * Read only. One GET per probed path, plus one more per path when
 * GITHUB_CONFIRM is set. Nothing is written and the repair is printed rather
 * than performed.
 *
 * Environment:
 *   GITHUB_TOKEN     a token with read access to the repository
 *   GITHUB_REPO      owner/name
 *   GITHUB_PER_PAGE  the page size to ask for, default 500
 *   GITHUB_CONFIRM   set to spend a second request per path at per_page=100
 */
const API = 'https://api.github.com';
const UA = 'github-per-page-clamp/1.0';

/** The documented ceiling on a page. */
export const MAX_PER_PAGE = 100;

// Anchored on the angle brackets rather than split on commas: a pagination URL
// can contain a comma of its own, and splitting on it breaks the link.
const LINK = /<([^>]+)>\s*;\s*rel="([^"]+)"/g;

const PROBES = ['issues', 'pulls', 'branches'];

/** Parse a Link header into {rel: url}. Pure. */
export function parseLink(header) {
  const out = {};
  if (!header) return out;
  for (const m of String(header).matchAll(LINK)) out[m[2]] = m[1];
  return out;
}

/** The page size GitHub will actually use for this request. Pure. */
export function clampedTo(requested) {
  const n = Number(requested);
  if (!Number.isFinite(n) || Math.trunc(n) < 1) return null;
  return Math.min(Math.trunc(n), MAX_PER_PAGE);
}

/** Whether this value will be lowered before it is served. Pure. */
export function isOverMaximum(requested) {
  const n = Number(requested);
  return Number.isFinite(n) && n > MAX_PER_PAGE;
}

/** The buggy predicate: fewer items than asked for, so that was the end. Pure. */
export function stopsOnShortPage(requested, received) {
  const size = clampedTo(requested);
  const got = Number(received);
  if (size === null || !Number.isFinite(got)) return false;
  return got < Number(requested);
}

/** The correct predicate: the header no longer advertises a next page. Pure. */
export function stopsOnMissingNext(links) {
  return !(links && Object.prototype.hasOwnProperty.call(links, 'next'));
}

/** Whether the short-page check would stop while the header says otherwise. */
export function predicatesDisagree(requested, received, links) {
  return stopsOnShortPage(requested, received) && !stopsOnMissingNext(links);
}

/** Classify one response. Pure. Returns [state, detail]. */
export function verdict(requested, received, links) {
  const size = clampedTo(requested);
  const got = Number(received);
  if (size === null || received === null || received === undefined || !Number.isFinite(got)) {
    return ['unknown', 'the request was not answered in a form this check can read.'];
  }
  const more = !stopsOnMissingNext(links);
  const over = isOverMaximum(requested);

  if (predicatesDisagree(requested, received, links)) {
    if (over && got === MAX_PER_PAGE) {
      return ['clamped-and-truncated',
        `per_page=${requested} was reduced to ${MAX_PER_PAGE} and rel="next" is `
        + 'present, so a client that stops on a short page stops here with more '
        + 'to read.'];
    }
    return ['smaller-maximum',
      `per_page=${requested} was asked for and ${got} item(s) came back with `
      + 'rel="next" still present, so this endpoint serves a smaller page than '
      + 'you requested and a short-page check stops here too.'];
  }
  if (over && got === MAX_PER_PAGE) {
    return ['clamped-at-boundary',
      `per_page=${requested} was reduced to ${MAX_PER_PAGE} and there is no next `
      + 'page, so this collection happens to end exactly on the boundary. The '
      + `clamp is real and the truncation starts on item ${MAX_PER_PAGE + 1}.`];
  }
  if (over) {
    return ['clamped-untested',
      `per_page=${requested} was reduced to ${MAX_PER_PAGE}, but only ${got} `
      + 'item(s) exist here, so the truncation cannot be shown on this path. The '
      + `clamp still applies to every path that grows past ${MAX_PER_PAGE}.`];
  }
  if (more) {
    return ['within-cap-more-pages',
      `per_page=${requested} was served in full and rel="next" is present. The `
      + 'short-page check agrees with the header here, which is luck rather than '
      + 'correctness.'];
  }
  return ['within-cap-complete',
    `per_page=${requested} was served in full and there is no next page. One `
    + 'request really is the whole list here.'];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (['clamped-and-truncated', 'clamped-at-boundary', 'clamped-untested'].includes(state)) {
    return 'send per_page=100 and terminate on the absence of rel="next" in the '
      + 'Link header. Asking for more than 100 buys nothing: not a bigger page, '
      + 'not fewer requests, not an error telling you so.';
  }
  if (state === 'smaller-maximum') {
    return 'this endpoint serves a smaller page than 100, so hard-coding any '
      + 'page size as your terminating condition is unsafe here. Follow '
      + 'rel="next" until it is absent.';
  }
  if (state === 'within-cap-more-pages') {
    return 'nothing on the page size. Check that the loop terminates on the '
      + 'missing rel="next" rather than on the page length: the two agree on '
      + 'this response and will part company on a clamp.';
  }
  if (state === 'within-cap-complete') return 'nothing.';
  return 'point the check at a path this token can list.';
}

/** Requests this run will spend against the core quota. Pure. */
export function readCost(paths, confirm = false) {
  const n = Array.isArray(paths) ? paths.length : 0;
  return n * (confirm ? 2 : 1);
}

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
}

async function get(token, path, params) {
  const url = new URL(API + path);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, String(v));
  const res = await fetch(url, { headers: headers(token) });
  const links = parseLink(res.headers.get('link'));
  if (!res.ok) return { status: res.status, items: null, links };
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return { status: res.status, items: Array.isArray(body) ? body : null, links };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const repo = (process.env.GITHUB_REPO || "dummy-github-repo");
  if (!token || !repo) {
    console.error('set GITHUB_TOKEN (read-only is enough) and GITHUB_REPO=owner/name');
    process.exitCode = 2;
    return;
  }
  const perPage = Number((process.env.GITHUB_PER_PAG || "dummy-github-per-pag")E || 500);
  const confirm = Boolean((process.env.GITHUB_CONFIRM || "dummy-github-confirm"));
  const paths = PROBES.map((name) => `/repos/${repo}/${name}`);
  console.log(`read cost: ${readCost(paths, confirm)} request(s) against the core hourly quota`);

  const findings = [];
  for (const path of paths) {
    const { status, items, links } = await get(token, path, { per_page: perPage });
    if (items === null) {
      console.log(`${path} returned ${status}; skipping it`);
      continue;
    }
    const [state, detail] = verdict(perPage, items.length, links);
    console.log(`${path}: asked for ${perPage}, received ${items.length}`);
    console.log(`${state}: ${detail}`);
    console.log(`repair: ${repair(state)}`);

    let honest = null;
    if (confirm) {
      const second = await get(token, path, { per_page: MAX_PER_PAGE });
      honest = second.items === null ? null : second.items.length;
      if (honest !== null) {
        console.log(`${path}: at per_page=${MAX_PER_PAGE} the same call returns ${honest} item(s)`);
      }
    }

    findings.push({
      path,
      status,
      requested: perPage,
      effective_page_size: clampedTo(perPage),
      received: items.length,
      rels: Object.keys(links).sort(),
      short_page_check_stops: stopsOnShortPage(perPage, items.length),
      header_check_stops: stopsOnMissingNext(links),
      predicates_disagree: predicatesDisagree(perPage, items.length, links),
      at_per_page_100: honest,
      state,
      detail,
    });
  }

  console.log(JSON.stringify({ requests_spent: readCost(paths, confirm), findings }, null, 2));
  const bad = ['clamped-and-truncated', 'smaller-maximum', 'clamped-at-boundary'];
  process.exitCode = findings.some((f) => bad.includes(f.state)) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
