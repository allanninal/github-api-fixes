/**
 * Find GitHub endpoints that ignore the page parameter instead of rejecting it.
 *
 * Read only. Two GETs per probed path at per_page=1, and nothing is written.
 *
 * Two independent signals are used, because either alone is unsafe: identical
 * identifiers across page 1 and page 2, and the parameter names on the
 * endpoint's own next link. The second does not depend on timing.
 *
 * Environment:
 *   GITHUB_TOKEN  a token with read access to the repository
 *   GITHUB_REPO   owner/name
 */
const API = 'https://api.github.com';
const UA = 'github-page-param-ignored/1.0';

const LINK = /<([^>]+)>\s*;\s*rel="([^"]+)"/g;

/** Cursor names first: an endpoint offering both is a cursor endpoint. */
export const CURSOR_PARAMS = ['after', 'before', 'cursor'];
export const OFFSET_PARAMS = ['page'];

/** Tried in order. Not every list on this API keys its items the same way. */
export const ID_FIELDS = ['id', 'node_id', 'sha', 'url'];

const PROBES = ['activity', 'events'];

/** Parse a Link header into {rel: url}. Pure. */
export function parseLink(header) {
  const out = {};
  if (!header) return out;
  for (const m of String(header).matchAll(LINK)) out[m[2]] = m[1];
  return out;
}

/** The query parameter names on the next URL, sorted. Pure. */
export function linkParams(links) {
  const url = (links || {}).next;
  if (!url) return [];
  try {
    return [...new Set([...new URL(url, API).searchParams.keys()])].sort();
  } catch {
    return [];
  }
}

/** What the endpoint's own next link is built from: cursor, offset or none. */
export function linkStyle(links) {
  const names = new Set(linkParams(links));
  if (CURSOR_PARAMS.some((n) => names.has(n))) return 'cursor';
  if (OFFSET_PARAMS.some((n) => names.has(n))) return 'offset';
  return 'none';
}

/** The cursor parameter this endpoint actually uses, or null. Pure. */
export function cursorHint(links) {
  const names = new Set(linkParams(links));
  return CURSOR_PARAMS.find((n) => names.has(n)) ?? null;
}

/** A stable identifier for one list item, or null. Pure. */
export function identity(item) {
  if (!item || typeof item !== 'object' || Array.isArray(item)) return null;
  for (const field of ID_FIELDS) {
    const value = item[field];
    if (value !== null && value !== undefined && value !== '') return String(value);
  }
  return null;
}

/** Identifiers for a page, dropping items that have none. Pure. */
export function identities(items) {
  if (!Array.isArray(items)) return [];
  return items.map(identity).filter((v) => v !== null);
}

/** Whether page two is page one, exactly. Pure. */
export function sameRows(first, second) {
  if (!first || !second || !first.length || !second.length) return false;
  return first.length === second.length && first.every((v, i) => v === second[i]);
}

/** Whether the two pages share any row at all. Pure. */
export function overlaps(first, second) {
  const set = new Set(first || []);
  return (second || []).some((v) => set.has(v));
}

/** Classify one endpoint from both signals. Pure. Returns [state, detail]. */
export function verdict(style, firstIds, secondIds) {
  if (!firstIds || !firstIds.length) {
    return ['inconclusive-empty',
      'page 1 returned nothing this check could identify, so there is no '
      + 'comparison to make. Point it at a path with rows in it.'];
  }
  if (!secondIds || !secondIds.length) {
    return ['offset-honoured',
      'page 2 came back empty, so the collection ends inside page 1 and the '
      + 'page parameter is being read.'];
  }
  if (sameRows(firstIds, secondIds)) {
    if (style === 'cursor' || style === 'none') {
      const shape = style === 'cursor'
        ? 'built from a cursor'
        : 'absent, so there is no next page to follow';
      return ['ignores-page',
        `page=2 returned the same row(s) as page=1 and the next link is ${shape}, `
        + 'so this endpoint does not read page at all. A loop that stops on a '
        + 'short page has no terminating condition here.'];
    }
    return ['suspect-ignores-page',
      'page=2 returned the same row(s) as page=1, but the next link is still '
      + 'built from page=, so this may be a feed that moved between the two '
      + 'requests. Re-run it, or add a stable sort, before treating it as a finding.'];
  }
  if (overlaps(firstIds, secondIds)) {
    return ['overlapping-pages',
      'page 1 and page 2 share rows without being identical, which is an '
      + 'unstable sort rather than an ignored parameter. Paging this endpoint '
      + 'will double-count and skip.'];
  }
  if (style === 'cursor') {
    return ['cursor-pagination',
      'the rows differ and the next link is built from a cursor, so this '
      + 'endpoint pages correctly and simply not by number. Follow its next URL '
      + 'rather than incrementing anything.'];
  }
  return ['offset-honoured',
    'page 2 returned different rows and the next link is built from page=, so '
    + 'offset pagination works here.'];
}

/** Whether a page-counting loop against this endpoint would ever end. Pure. */
export function loopTerminates(state) {
  return !['ignores-page', 'suspect-ignores-page'].includes(state);
}

/** The sentence a reader has to act on. Pure. */
export function repair(state, links = null) {
  if (state === 'ignores-page') {
    const cursor = cursorHint(links);
    if (cursor) {
      return 'follow the next URL from the Link header verbatim, using '
        + `${cursor}=. Do not construct it, and do not send page: the value is `
        + 'opaque and incrementing anything here is meaningless.';
    }
    return 'stop paging this endpoint by number. It advertises no next page, so '
      + 'one request is what it offers, and the GraphQL equivalent with '
      + 'after: $cursor is the way to walk more.';
  }
  if (state === 'suspect-ignores-page') {
    return 're-run the check, or add a deterministic sort, before changing any '
      + 'code. Identical rows on a recency-ordered feed can be two requests a '
      + 'second apart rather than an ignored parameter.';
  }
  if (state === 'overlapping-pages') {
    return 'sort deterministically before paging, or switch to the cursor form. '
      + 'Offset paging over a feed that reorders will double-count some rows and '
      + 'miss others whatever the page size.';
  }
  if (state === 'cursor-pagination') {
    return 'follow the next URL from the Link header verbatim. It already '
      + 'carries the cursor, and building it yourself is the only way to get '
      + 'this wrong.';
  }
  if (state === 'inconclusive-empty') return 'point the check at a path that has rows in it.';
  return 'nothing.';
}

/** Requests this run will spend against the core quota. Pure. */
export function readCost(paths) {
  return 2 * (Array.isArray(paths) ? paths.length : 0);
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
  if (res.status !== 200) return { status: res.status, items: null, links };
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
  const paths = PROBES.map((name) => `/repos/${repo}/${name}`);
  console.log(`read cost: ${readCost(paths)} request(s) against the core hourly quota`);

  const findings = [];
  for (const path of paths) {
    const one = await get(token, path, { per_page: 1, page: 1 });
    if (one.items === null) {
      console.log(`${path} returned ${one.status}; skipping it`);
      continue;
    }
    const two = await get(token, path, { per_page: 1, page: 2 });
    const firstIds = identities(one.items);
    const secondIds = identities(two.items);
    const style = linkStyle(one.links);
    const [state, detail] = verdict(style, firstIds, secondIds);

    if (sameRows(firstIds, secondIds)) {
      console.log(`${path}: page=1 and page=2 returned the same id(s)`);
    }
    console.log(`${state}: ${detail}`);
    console.log(`a page-counting loop here ${loopTerminates(state) ? 'terminates' : 'never terminates'}`);
    console.log(`repair: ${repair(state, one.links)}`);

    findings.push({
      path,
      status: [one.status, two.status],
      next_link_params: linkParams(one.links),
      link_style: style,
      cursor_parameter: cursorHint(one.links),
      page_1_ids: firstIds,
      page_2_ids: secondIds,
      identical: sameRows(firstIds, secondIds),
      loop_terminates: loopTerminates(state),
      state,
      detail,
    });
  }

  console.log(JSON.stringify({ requests_spent: readCost(paths), findings }, null, 2));
  const bad = ['ignores-page', 'suspect-ignores-page', 'overlapping-pages'];
  process.exitCode = findings.some((f) => bad.includes(f.state)) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
