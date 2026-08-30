/**
 * Show that GraphQL search stops at the same 1,000 results REST does.
 *
 * Read only, and queries only. GitHub's GraphQL endpoint takes its document in
 * the request body, so a read travels by POST there exactly as a write would.
 * The document is parsed first and anything containing a mutation or a
 * subscription is refused before a socket opens.
 *
 * The search connection is served by the same index as GET /search/* and
 * inherits the same ceiling of roughly 1,000 retrievable results. REST says so
 * with 422 Validation Failed on page 11; GraphQL just sets hasNextPage to
 * false and lets the walk finish normally.
 *
 * Environment:
 *   GITHUB_TOKEN        a token with read access to the GraphQL API
 *   GITHUB_SEARCH       the search string
 *   GITHUB_SEARCH_TYPE  ISSUE, REPOSITORY, USER or DISCUSSION
 *   GITHUB_MAX_PAGES    how many pages of 100 to walk
 */
const API = 'https://api.github.com';
const UA = 'github-graphql-search-ceiling/1.0';

/** The retrievable-result ceiling on the search index, both APIs alike. */
export const SEARCH_RESULT_CEILING = 1000;

/** The largest page any GraphQL connection will serve. */
export const MAX_PAGE_SIZE = 100;

/** One search connection at first: 100 costs one point. */
export const POINTS_PER_PAGE = 1;

const SEARCH_QUERY = 'query($q: String!, $type: SearchType!, $after: String) {'
  + ' search(query: $q, type: $type, first: 100, after: $after) {'
  + ' issueCount repositoryCount userCount'
  + ' pageInfo { hasNextPage endCursor }'
  + ' nodes { __typename } } }';

/** Connections that answer an inventory question without the index. */
export const TYPED_CONNECTIONS = {
  ISSUE: 'repository.issues or repository.pullRequests',
  REPOSITORY: 'organization.repositories or user.repositories',
  USER: 'organization.membersWithRole',
  DISCUSSION: 'repository.discussions',
};

/** Remove GraphQL comments and string literals from a document. Pure. */
export function stripNoise(document) {
  const src = String(document ?? '');
  const out = [];
  let i = 0;
  while (i < src.length) {
    const ch = src[i];
    if (ch === '#') {
      while (i < src.length && src[i] !== '\n') i += 1;
      continue;
    }
    if (src.startsWith('"""', i)) {
      const j = src.indexOf('"""', i + 3);
      i = j < 0 ? src.length : j + 3;
      out.push(' ');
      continue;
    }
    if (ch === '"') {
      i += 1;
      while (i < src.length && src[i] !== '"') i += src[i] === '\\' ? 2 : 1;
      i += 1;
      out.push(' ');
      continue;
    }
    out.push(ch);
    i += 1;
  }
  return out.join('');
}

/** The top-level operations in a document, in order. Pure. */
export function operations(document) {
  const src = `${stripNoise(document)} `;
  const ops = [];
  let depth = 0;
  let word = '';
  let declared = null;
  for (const ch of src) {
    if (/[A-Za-z0-9_]/.test(ch)) { word += ch; continue; }
    if (word) {
      if (depth === 0 && ['query', 'mutation', 'subscription', 'fragment'].includes(word)) {
        declared = word;
      }
      word = '';
    }
    if (ch === '{') {
      if (depth === 0) { ops.push(declared || 'query'); declared = null; }
      depth += 1;
    } else if (ch === '}') {
      depth = Math.max(0, depth - 1);
    }
  }
  return ops;
}

/** Why this document will not be sent, or null if it is a read. Pure. */
export function refusal(document) {
  const ops = operations(document);
  if (ops.length === 0) return 'the document contains no operation to send.';
  for (const kind of ['mutation', 'subscription']) {
    if (ops.includes(kind)) {
      return `the document contains a ${kind}. This script sends queries only: `
        + 'a query is a read, and the section it belongs to promises its '
        + 'scripts never write.';
    }
  }
  return null;
}

function asCount(total) {
  const n = Number(total);
  return Number.isFinite(n) ? Math.max(0, Math.trunc(n)) : 0;
}

/** How many of the matches can actually be paged to. Pure. */
export function reachable(total) {
  return Math.min(asCount(total), SEARCH_RESULT_CEILING);
}

/** How many matches exist that no cursor will ever reach. Pure. */
export function unreachable(total) {
  return Math.max(0, asCount(total) - SEARCH_RESULT_CEILING);
}

/** How many pages of this size fit under the ceiling. Pure. */
export function pagesToCeiling(pageSize = MAX_PAGE_SIZE) {
  const size = Math.min(Math.max(1, Math.trunc(Number(pageSize) || 1)), MAX_PAGE_SIZE);
  return Math.ceil(SEARCH_RESULT_CEILING / size);
}

/** How many under-the-ceiling slices a partition needs. Pure. */
export function slicesNeeded(total) {
  const n = asCount(total);
  return n ? Math.ceil(n / SEARCH_RESULT_CEILING) : 0;
}

/** The ceiling-free connection that answers the same question. Pure. */
export function typedConnectionFor(searchType) {
  const key = String(searchType ?? '').toUpperCase();
  return TYPED_CONNECTIONS[key] || 'the typed connection for this object type';
}

/** How each API announces the same ceiling. Pure. */
export function truncationSignal(protocol) {
  if (String(protocol).toLowerCase() === 'rest') {
    return '422 Validation Failed on page 11, with the message "Only the first '
      + '1000 search results are available".';
  }
  return 'no error at all. pageInfo.hasNextPage turns false and the walk '
    + 'terminates the way a complete walk terminates.';
}

/** How this walk ended, and whether that ending was honest. Pure. */
export function classifyWalk(total, collected, hasNextPage, pagesWalked, maxPages) {
  const matches = asCount(total);
  const got = asCount(collected);
  if (hasNextPage && pagesWalked >= maxPages) {
    return ['stopped-early-by-request', 'the walk stopped at the --max-pages '
      + 'limit with more pages available, so nothing about the ceiling is '
      + `proved yet. ${got} of ${matches} node(s) collected.`];
  }
  if (hasNextPage) {
    return ['still-paging', 'the connection still reports another page. Keep '
      + 'walking or raise --max-pages.'];
  }
  if (got >= SEARCH_RESULT_CEILING && matches > got) {
    return ['ceiling-hit-silently', `pagination stopped after ${got} node(s) `
      + `with the index reporting ${matches} match(es). No error was raised and `
      + 'hasNextPage simply turned false.'];
  }
  if (matches > got) {
    return ['truncated-early', `the walk ended with ${got} of ${matches} `
      + 'match(es) and below the ceiling, which is not this note: check for a '
      + 'timed-out search or a filter applied after the count.'];
  }
  return ['complete', `${got} node(s) collected against ${matches} match(es). `
    + 'This query is under the ceiling and the answer is whole.'];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state, total, searchType) {
  if (state === 'ceiling-hit-silently') {
    return `for an inventory use the typed connection ${typedConnectionFor(searchType)}, `
      + `which has no ceiling. For a genuine search, partition into at least `
      + `${slicesNeeded(total)} slice(s) by created: date range and union them.`;
  }
  if (state === 'truncated-early') {
    return 'see /github/search-incomplete-results/ -- a search that ends below '
      + 'the ceiling ended for a different reason, and that one is not '
      + 'deterministic.';
  }
  if (state === 'still-paging') {
    return 'nothing yet. Walk to the end, or read issueCount on page one and '
      + `compare it against ${SEARCH_RESULT_CEILING}.`;
  }
  if (state === 'stopped-early-by-request') {
    return `raise --max-pages to at least ${pagesToCeiling()} to reach the `
      + 'ceiling, or trust issueCount, which already tells you.';
  }
  return 'request issueCount alongside nodes and refuse to publish a result set '
    + 'shorter than it without labelling it truncated.';
}

/** The most this run can spend against the hourly budget. Pure. */
export function pointCost(maxPages) {
  return asCount(maxPages) * POINTS_PER_PAGE;
}

/** The index's own count for this search type. Pure. */
export function matchCount(search, searchType) {
  if (!search || typeof search !== 'object') return 0;
  const key = { ISSUE: 'issueCount', REPOSITORY: 'repositoryCount', USER: 'userCount' }[
    String(searchType ?? '').toUpperCase()] || 'issueCount';
  return asCount(search[key]);
}

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'Content-Type': 'application/json',
    'User-Agent': UA,
  };
}

async function runQuery(token, document, variables) {
  const res = await fetch(`${API}/graphql`, {
    // A GraphQL query is a read. POST is only how the document reaches the
    // endpoint, and refusal() has already rejected anything that is not a read.
    method: 'POST',
    headers: headers(token),
    body: JSON.stringify({ query: document, variables: variables || {} }),
  });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return { status: res.status, body };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const search = (process.env.GITHUB_SEARCH || "dummy-github-search");
  if (!token || !search) {
    console.error('set GITHUB_TOKEN (read-only is enough) and GITHUB_SEARCH');
    process.exitCode = 2;
    return;
  }
  const type = ((process.env.GITHUB_SEARCH_TYP || "dummy-github-search-typ")E || 'ISSUE').toUpperCase();
  const maxPages = Number((process.env.GITHUB_MAX_PAGE || "dummy-github-max-page")S || 11);

  const whyNot = refusal(SEARCH_QUERY);
  if (whyNot) {
    console.error(`refusing to send: ${whyNot}`);
    process.exitCode = 2;
    return;
  }
  console.log(`point cost: up to ${pointCost(maxPages)} point(s) against the `
    + '5,000/hour GraphQL budget');

  let cursor = null;
  let collected = 0;
  let total = 0;
  let pages = 0;
  let hasNext = false;
  while (pages < maxPages) {
    // eslint-disable-next-line no-await-in-loop
    const { status, body } = await runQuery(token, SEARCH_QUERY,
      { q: search, type, after: cursor });
    if (!body || body.errors) {
      console.error(`the search itself failed: HTTP ${status} `
        + `${JSON.stringify((body || {}).errors || []).slice(0, 300)}`);
      process.exitCode = 2;
      return;
    }
    const node = ((body.data || {}).search) || {};
    const nodes = node.nodes || [];
    const info = node.pageInfo || {};
    pages += 1;
    collected += nodes.length;
    total = matchCount(node, type);
    hasNext = Boolean(info.hasNextPage);
    cursor = info.endCursor;
    console.log(`page ${pages}: ${nodes.length} node(s), collected=${collected}, `
      + `matches=${total}, hasNextPage=${hasNext ? 'yes' : 'no'}`);
    if (!hasNext) break;
  }

  const [state, detail] = classifyWalk(total, collected, hasNext, pages, maxPages);
  console.log(`${state}: ${detail}`);
  console.log(`reachable: ${reachable(total)}    unreachable: ${unreachable(total)}`);
  console.log(`the REST twin of this stop is ${truncationSignal('rest')}`);
  console.log(`here it is ${truncationSignal('graphql')}`);
  console.log(`repair: ${repair(state, total, type)}`);

  console.log(JSON.stringify({
    points_spent: pages * POINTS_PER_PAGE,
    search,
    type,
    matches: total,
    collected,
    pages_walked: pages,
    has_next_page: hasNext,
    reachable: reachable(total),
    unreachable: unreachable(total),
    slices_needed: slicesNeeded(total),
    typed_connection: typedConnectionFor(type),
    state,
    detail,
  }, null, 2));
  process.exitCode = state === 'ceiling-hit-silently' ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
