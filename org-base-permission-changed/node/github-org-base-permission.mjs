/**
 * Say whether an organization's base permission is why the repository list shrank.
 *
 * Read only. Three GETs. Granting access to a repository is a write and
 * somebody with admin has to make it, so this script measures the loss and
 * prints the narrow repair.
 *
 * default_repository_permission is the role every member holds on
 * repositories they were never explicitly added to. Moving it from read to
 * none removes implicit access everywhere at once, so the integration keeps
 * succeeding while covering a fraction of what it did yesterday.
 *
 * Environment:
 *   GITHUB_TOKEN    a read-only token for the account whose coverage shrank
 *   GITHUB_ORG      the organization whose repositories shrank
 *   GITHUB_EXPECT   optional base permission you configured against
 */
const API = 'https://api.github.com';
const UA = 'github-org-base-permission/1.0';

/** Weakest first. The documented values of default_repository_permission. */
export const BASE_PERMISSIONS = ['none', 'read', 'write', 'admin'];

/** What a member with no explicit grants gets, per base permission. */
export const IMPLIES = {
  none: 'members get no role on repositories they were not added to '
    + 'individually or through a team. Every private repository in the '
    + 'organization is invisible to a member with no explicit grants.',
  read: 'every member can read every repository in the organization without '
    + 'being added to it.',
  write: 'every member can push to every repository in the organization '
    + 'without being added to it.',
  admin: 'every member administers every repository in the organization. This '
    + 'is rare and worth questioning on its own.',
};

/** Requests this run will spend against the core quota. Pure. */
export function readCost() {
  return 3;
}

/** Position in the hierarchy, or -1 for something unrecognised. Pure. */
export function baseRank(value) {
  return BASE_PERMISSIONS.indexOf(String(value ?? '').trim().toLowerCase());
}

/** The organization's base permission. Pure. [value, detail]. */
export function baseState(orgPayload) {
  if (!orgPayload || typeof orgPayload !== 'object') {
    return [null, 'no organization payload was read.'];
  }
  if (!Object.prototype.hasOwnProperty.call(orgPayload, 'default_repository_permission')) {
    return [null, 'default_repository_permission was not returned. Reading it '
      + 'needs organization access, so this is unreadable rather than absent.'];
  }
  const value = String(orgPayload.default_repository_permission ?? '')
    .trim().toLowerCase();
  if (baseRank(value) < 0) {
    return [value || null, `the value '${value}' is not one of the four `
      + 'documented base permissions.'];
  }
  return [value, IMPLIES[value]];
}

/** Split a Link header into its entries. Pure. No regular expression.
 *
 * Split on the commas that separate entries and not on the ones inside a URL,
 * because a URL in a Link header can carry commas of its own.
 */
export function linkParts(linkHeader) {
  const parts = [];
  let current = '';
  let depth = 0;
  for (const ch of String(linkHeader ?? '')) {
    if (ch === '<') depth += 1;
    else if (ch === '>') depth = Math.max(0, depth - 1);
    if (ch === ',' && depth === 0) { parts.push(current); current = ''; } else current += ch;
  }
  if (current.trim()) parts.push(current);
  return parts;
}

/** The page number of rel="last", or null. Pure. No regular expression. */
export function lastPageFromLink(linkHeader) {
  if (!linkHeader) return null;
  for (const part of linkParts(linkHeader)) {
    if (!part.includes('rel="last"') && !part.includes('rel=last')) continue;
    const start = part.indexOf('<');
    const end = part.indexOf('>', start + 1);
    if (start < 0 || end < 0) continue;
    const url = part.slice(start + 1, end);
    const query = url.includes('?') ? url.slice(url.indexOf('?') + 1) : '';
    for (const field of query.split('&')) {
      const cut = field.indexOf('=');
      const name = cut < 0 ? field : field.slice(0, cut);
      const value = cut < 0 ? '' : field.slice(cut + 1);
      if (name === 'page' && value.length && [...value].every((c) => c >= '0' && c <= '9')) {
        return Number(value);
      }
    }
  }
  return null;
}

/** How many items the collection holds, at per_page=1. Pure. [count, how]. */
export function countFromLink(linkHeader, returned) {
  const last = lastPageFromLink(linkHeader);
  if (last !== null) return [last, 'from rel="last" with per_page=1'];
  if (!returned) return [0, 'the first page came back empty and carried no rel="last"'];
  return [Number(returned), 'a single page with no rel="last", so this is what '
    + 'came back rather than a measured count'];
}

/** How many repositories the organization holds. Pure. [count, detail]. */
export function orgTotal(orgPayload) {
  if (!orgPayload || typeof orgPayload !== 'object') {
    return [null, 'no organization payload was read.'];
  }
  const pub = orgPayload.public_repos;
  const priv = orgPayload.total_private_repos;
  if ((pub === null || pub === undefined) && (priv === null || priv === undefined)) {
    return [null, 'neither repository count was returned, which needs '
      + 'organization access.'];
  }
  const total = Number(pub || 0) + Number(priv || 0);
  return [total, `public ${pub ?? 'unreadable'} + private ${priv ?? 'unreadable'}`];
}

/** Grade what the account can see against what the org holds. Pure. */
export function coverageState(visible, total) {
  if (total === null || total === undefined || visible === null
      || visible === undefined) {
    return 'unknown';
  }
  if (total <= 0) return 'nothing-to-cover';
  if (visible >= total) return 'full';
  if (visible === 0 || visible * 20 < total) return 'collapsed';
  if (visible * 2 < total) return 'shrunken';
  return 'partial';
}

/** Compare the configured base permission against the live one. Pure. */
export function drift(expected, actual) {
  if (!expected || actual === null || actual === undefined) {
    return ['drift-unknown', 'no expected base permission was supplied, or the '
      + 'live one could not be read, so there is nothing to compare.'];
  }
  const want = baseRank(expected);
  const have = baseRank(actual);
  if (want < 0 || have < 0) {
    return ['drift-unknown', 'one of the two values is not a documented base '
      + 'permission.'];
  }
  if (want === have) {
    return ['base-unchanged', 'the organization still reports the base '
      + 'permission this integration was configured against.'];
  }
  if (have < want) {
    return ['base-tightened', `configured for '${expected}', the organization `
      + `now says '${actual}'. That is one field and it re-graded every `
      + 'repository at once.'];
  }
  return ['base-loosened', `configured for '${expected}', the organization now `
    + `says '${actual}', which grants more implicit access than you expected `
    + 'rather than less.'];
}

/** The finding, in one state. Pure. [state, detail]. */
export function verdict(base, coverage) {
  if (base === null || base === undefined) {
    return ['base-unreadable', 'the base permission could not be read, so the '
      + 'coverage number stands on its own. Read it with a token that has '
      + 'organization access before concluding anything about the default.'];
  }
  if (base === 'none' && ['collapsed', 'shrunken'].includes(coverage)) {
    return ['base-none-implicit-access-gone', 'base permission is none and this '
      + 'account reaches a fraction of the organization. The repositories it '
      + 'still reaches are the ones it was added to explicitly; the rest were '
      + 'never granted, only defaulted.'];
  }
  if (base === 'none' && ['full', 'partial'].includes(coverage)) {
    return ['base-none-explicit-grants-hold', 'base permission is none and '
      + "coverage is largely intact, which means this account's access is "
      + 'explicit. It is not exposed to this change.'];
  }
  if (base !== 'none' && ['collapsed', 'shrunken'].includes(coverage)) {
    return ['coverage-lost-elsewhere', 'the base permission still grants '
      + 'implicit access and the coverage is short anyway, so the loss is not '
      + "this field. Membership, SSO authorization and an App's repository "
      + 'selection are the other ways a list gets shorter.'];
  }
  if (coverage === 'nothing-to-cover') {
    return ['nothing-to-cover', 'the organization reports no repositories, so '
      + 'there is no coverage question to answer.'];
  }
  return ['coverage-as-expected', 'the account reaches what the base permission '
    + 'implies it should. Nothing here explains a shorter list.'];
}

/** The narrow repair. Pure. Nothing here is executed. */
export function repair(state, org) {
  if (state === 'base-none-implicit-access-gone') {
    return `add this account, or a team it belongs to, to the repositories the `
      + `job is meant to cover in ${org}. Do not raise the base permission `
      + 'back: that re-grants implicit access to every member of the '
      + 'organization to fix one integration.';
  }
  if (state === 'coverage-lost-elsewhere') {
    return 'look past the base permission. Check that the account is still a '
      + 'member, that the token is SSO-authorized where that applies, and, for '
      + 'a GitHub App, that the installation covers the repositories you expect.';
  }
  if (state === 'base-unreadable') {
    return 're-read the organization with a token that has organization access. '
      + 'Until then the coverage number is a measurement without an explanation.';
  }
  if (state === 'base-none-explicit-grants-hold') {
    return 'nothing. Keep it that way: explicit grants are what makes this '
      + 'account immune to the next change to the default.';
  }
  return 'nothing on the base permission. The shorter list, if there is one, '
    + 'has another cause.';
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
  const org = (process.env.GITHUB_ORG || "dummy-github-org");
  if (!token || !org) {
    console.error('set GITHUB_TOKEN (read-only is enough) and GITHUB_ORG');
    process.exitCode = 2;
    return;
  }
  const expect = (process.env.GITHUB_EXPEC || "dummy-github-expec")T || '';
  console.log(`read cost: ${readCost()} request(s) against the core hourly quota`);

  const orgResponse = await fetch(`${API}/orgs/${org}`, { headers: headers(token) });
  const orgPayload = orgResponse.status === 200 ? await orgResponse.json() : {};
  const [base, baseDetail] = baseState(orgPayload);
  console.log(`base permission: ${base || 'unreadable'} — ${baseDetail}`);

  const [driftState, driftDetail] = drift(expect, base);
  console.log(`drift: ${driftState} — ${driftDetail}`);

  const mine = await fetch(
    `${API}/user/repos?affiliation=organization_member&per_page=1`,
    { headers: headers(token) },
  );
  const body = mine.status === 200 ? await mine.json() : [];
  const [visible, how] = countFromLink(mine.headers.get('link'),
    Array.isArray(body) ? body.length : 0);
  console.log(`visible through membership: ${visible} (${how})`);

  const [total, totalDetail] = orgTotal(orgPayload);
  console.log(`organization holds: ${total ?? 'unreadable'} repositories (${totalDetail})`);

  const coverage = coverageState(visible, total);
  console.log(`coverage: ${coverage} — ${visible} of ${total ?? 'unreadable'}`);

  const [state, detail] = verdict(base, coverage);
  console.log(`state: ${state} — ${detail}`);
  console.log(`repair: ${repair(state, org)}`);

  console.log(JSON.stringify({
    organization: org,
    default_repository_permission: base,
    expected_base_permission: expect || null,
    drift_state: driftState,
    visible_through_membership: visible,
    visible_source: how,
    organization_total: total,
    coverage,
    state,
    detail,
    repair: repair(state, org),
  }, null, 2));
  process.exitCode = ['base-none-implicit-access-gone',
    'coverage-lost-elsewhere'].includes(state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
