/**
 * Find archived repositories before a write loop discovers them the hard way.
 *
 * Read only. One GET per repository, or one per hundred in an organisation
 * sweep, and nothing is written. No write is attempted against an archived
 * repository to confirm the 403: the archived boolean is the finding and it
 * arrives before any write would be sent.
 *
 * Archiving makes a repository read-only. Reads keep working, every write is
 * refused with 403, and no token, scope or App permission changes that.
 *
 * Environment:
 *   GITHUB_TOKEN      a token with read access to the repositories
 *   GITHUB_REPOS      comma-separated owner/name values
 *   GITHUB_ORG        an organisation to sweep instead
 *   GITHUB_ATTEMPTS   retries an hour your write loop makes
 *   GITHUB_FAILURE    a refusal message you already recorded
 */
const API = 'https://api.github.com';
const UA = 'github-archived-repo-guard/1.0';

/** The core hourly quota a retrying client spends these requests out of. */
export const CORE_QUOTA_PER_HOUR = 5000;

/** One listing request covers this many repositories. */
export const ORG_PAGE_SIZE = 100;

/** Words that identify an archived repository in a recorded refusal. */
export const ARCHIVED_WORDS = ['archived', 'read-only', 'read only'];

/** Which platform state this repository is in. Pure. */
export function lifecycle(repo) {
  if (!repo || typeof repo !== 'object') return 'unknown';
  const archived = Boolean(repo.archived);
  const disabled = Boolean(repo.disabled);
  if (archived && disabled) return 'archived-and-disabled';
  if (archived) return 'archived';
  if (disabled) return 'disabled';
  return 'active';
}

/** Whether a write to this repository can ever be accepted. Pure. */
export function acceptsWrites(state) {
  if (['archived', 'disabled', 'archived-and-disabled'].includes(state)) return false;
  if (state === 'active') return true;
  return null;
}

/** What a client should do with a failure here. Pure. */
export function retryPolicy(state) {
  if (acceptsWrites(state) === false) return 'permanent-skip';
  if (state === 'active') return 'retry';
  return 'unknown';
}

/** Why this repository refuses writes, in one sentence. Pure. */
export function explain(state) {
  if (state === 'archived') {
    return 'archiving makes a repository read-only, so no write will ever be '
      + 'accepted here regardless of the token.';
  }
  if (state === 'disabled') {
    return 'the repository is disabled, which is a different state with a '
      + 'different owner: see the disabled repository note.';
  }
  if (state === 'archived-and-disabled') {
    return 'the repository is both archived and disabled. Unarchiving it would '
      + 'still leave it disabled, so the disabled state is the one to resolve first.';
  }
  if (state === 'active') {
    return 'the repository accepts writes; this refusal is about something else.';
  }
  return 'the repository could not be read, so its state is unknown.';
}

/** Attribute a refusal you already recorded. Pure. [state, detail]. */
export function classifyFailure(status, message) {
  const text = String(message ?? '').toLowerCase();
  const code = Number(status);

  if (ARCHIVED_WORDS.some((word) => text.includes(word))) {
    return ['archived-refusal', 'the message names the repository as archived, '
      + 'which is a property of the repository and not of your credential.'];
  }
  if (text.includes('rate limit')) {
    return ['rate-limited', 'a rate limit, which is a transient 403 and the one '
      + 'kind worth retrying. That is a different note.'];
  }
  if (text.includes('not accessible') || text.includes('integration')
    || text.includes('personal access token')) {
    return ['credential-refusal', 'the message blames the credential rather '
      + 'than the repository, so this is a permissions problem and widening the '
      + 'grant may actually help.'];
  }
  if (code === 404) {
    return ['not-found', '404 rather than 403, which means several things at '
      + 'once and needs its own triage.'];
  }
  if (code === 403) {
    return ['forbidden-unattributed', 'a 403 whose message names neither the '
      + 'repository state nor a rate limit. Read the repository object to settle it.'];
  }
  return ['no-failure', 'nothing here names a refusal.'];
}

/** Whole days between an ISO 8601 timestamp and now. Pure. Null if absent. */
export function daysSince(timestamp, now = Date.now()) {
  if (!timestamp) return null;
  const when = Date.parse(String(timestamp));
  if (!Number.isFinite(when)) return null;
  return Math.max(0, Math.floor((now - when) / 86400000));
}

/** Requests a retrying client spends on refusals that cannot succeed. Pure. */
export function wastedRequests(attemptsPerHour, repositories, hours = 1) {
  const rate = Math.max(0, Math.trunc(Number(attemptsPerHour) || 0));
  const count = Math.max(0, Math.trunc(Number(repositories) || 0));
  const span = Math.max(0, Math.trunc(Number(hours) || 0));
  return rate * count * span;
}

/** That spend as a whole-number percentage of the hourly quota. Pure. */
export function quotaShare(requestsPerHour, quota = CORE_QUOTA_PER_HOUR) {
  const spend = Math.max(0, Math.trunc(Number(requestsPerHour) || 0));
  if (!quota) return 0;
  return Math.round((100 * spend) / quota);
}

/** The repositories a write loop should never visit. Pure and sorted. */
export function skipList(rows) {
  const names = new Set();
  for (const row of rows || []) {
    if (!row || typeof row !== 'object') continue;
    if (acceptsWrites(row.state) === false && row.full_name) {
      names.add(String(row.full_name));
    }
  }
  return [...names].sort();
}

/** Counts for the bottom of the report. Pure. */
export function summarise(rows) {
  const counts = {
    total: 0, archived: 0, disabled: 0, writable: 0, unknown: 0,
  };
  for (const row of rows || []) {
    const state = (row || {}).state;
    counts.total += 1;
    if (['archived', 'archived-and-disabled'].includes(state)) counts.archived += 1;
    if (['disabled', 'archived-and-disabled'].includes(state)) counts.disabled += 1;
    if (state === 'active') counts.writable += 1;
    if (state === 'unknown') counts.unknown += 1;
  }
  return counts;
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (state === 'archived') {
    return 'filter archived repositories out at the top of the write loop and '
      + 'treat this as a permanent skip. Unarchive only if the repository is '
      + 'genuinely still in use.';
  }
  if (state === 'disabled') {
    return 'see /github/repo-disabled/ -- a disabled repository is a different '
      + 'state with a different owner, usually billing or a terms problem '
      + 'rather than a decision on your side.';
  }
  if (state === 'archived-and-disabled') {
    return 'resolve the disabled state with GitHub first; unarchiving on its '
      + 'own will not make this repository writable.';
  }
  if (state === 'active') {
    return 'nothing here. This repository accepts writes, so a refusal against '
      + 'it is about the credential or the branch.';
  }
  if (state === 'archived-refusal') {
    return 'stop retrying and skip. No token, scope or App permission makes an '
      + 'archived repository writable.';
  }
  if (state === 'rate-limited') {
    return 'honour retry-after and slow down. This one really is worth retrying.';
  }
  if (state === 'credential-refusal') {
    return 'triage the credential: the message blames the token or the '
      + 'integration rather than the repository state.';
  }
  return 'read the repository object and use the archived and disabled booleans '
    + 'rather than inferring state from a status code.';
}

/** Requests a per-repository run will spend. Pure. */
export function readCostForRepos(repos) {
  return (repos || []).length;
}

/** Listing requests an organisation of this size needs. Pure. */
export function pagesFor(count, pageSize = ORG_PAGE_SIZE) {
  const total = Math.max(0, Math.trunc(Number(count) || 0));
  if (!total) return 0;
  return Math.ceil(total / pageSize);
}

/** The Link header as {rel: url}. Pure. Comma-safe. */
export function parseLink(header) {
  const text = String(header ?? '');
  const links = {};
  let i = 0;
  for (;;) {
    const start = text.indexOf('<', i);
    if (start < 0) break;
    const end = text.indexOf('>', start);
    if (end < 0) break;
    const url = text.slice(start + 1, end);
    const tail = text.slice(end + 1);
    const stop = tail.indexOf('<');
    const segment = stop < 0 ? tail : tail.slice(0, stop);
    let rel = '';
    for (const raw of segment.split(';')) {
      const bit = raw.trim();
      if (bit.startsWith('rel=')) rel = bit.slice(4).trim().replace(/[",]/g, '');
    }
    if (rel) links[rel] = url;
    i = end + 1;
  }
  return links;
}

/** One report row from one repository object. Pure. */
export function rowFor(repo) {
  const state = lifecycle(repo);
  return {
    full_name: (repo || {}).full_name,
    state,
    accepts_writes: acceptsWrites(state),
    retry_policy: retryPolicy(state),
    explanation: explain(state),
    days_since_last_push: daysSince((repo || {}).pushed_at),
    repair: repair(state),
  };
}

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
}

async function listOrgRepos(token, org, maxPages = 20) {
  let url = `${API}/orgs/${org}/repos?type=all&per_page=${ORG_PAGE_SIZE}`;
  const repos = [];
  let spent = 0;
  while (url && spent < maxPages) {
    const res = await fetch(url, { headers: headers(token) });
    spent += 1;
    if (!res.ok) break;
    let page = null;
    try { page = await res.json(); } catch { page = null; }
    if (!Array.isArray(page)) break;
    repos.push(...page.filter((item) => item && typeof item === 'object'));
    url = parseLink(res.headers.get('link')).next;
  }
  return { repos, spent };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const org = (process.env.GITHUB_ORG || "dummy-github-org");
  const names = ((process.env.GITHUB_REPO || "dummy-github-repo")S || '').split(',')
    .map((n) => n.trim()).filter(Boolean);
  if (!token || (!org && !names.length)) {
    console.error('set GITHUB_TOKEN and either GITHUB_ORG or GITHUB_REPOS');
    process.exitCode = 2;
    return;
  }

  if (names.length) {
    console.log(`read cost: ${readCostForRepos(names)} request(s) against the `
      + 'core hourly quota');
  }
  if (org) {
    console.log(`read cost: 1 request(s) per ${ORG_PAGE_SIZE} repositories in `
      + 'an org sweep');
  }

  const rows = [];
  if (org) {
    const { repos, spent } = await listOrgRepos(token, org);
    console.log(`${org}: ${repos.length} repository(ies) read in ${spent} request(s)`);
    rows.push(...repos.map(rowFor));
  }
  for (const name of names) {
    const res = await fetch(`${API}/repos/${name}`, { headers: headers(token) });
    let body = null;
    try { body = await res.json(); } catch { body = null; }
    rows.push(res.status === 200 && body ? rowFor(body) : {
      full_name: name,
      state: 'unknown',
      accepts_writes: null,
      retry_policy: 'unknown',
      explanation: explain('unknown'),
      days_since_last_push: null,
      repair: repair('unknown'),
    });
  }

  const frozen = rows.filter((row) => row.accepts_writes === false);
  for (const row of frozen) {
    console.log(`${row.full_name}: ${row.state}`);
    console.log(`  ${row.retry_policy}: ${row.explanation}`);
    if (row.days_since_last_push !== null) {
      console.log(`  last push ${row.days_since_last_push} day(s) ago`);
    }
    console.log(`  repair: ${row.repair}`);
  }

  let recorded = null;
  const message = (process.env.GITHUB_FAILUR || "dummy-github-failur")E || '';
  if (message) {
    const [state, detail] = classifyFailure('', message);
    console.log(`recorded failure -> ${state}: ${detail}`);
    console.log(`repair: ${repair(state)}`);
    recorded = { state, detail };
  }

  const attempts = Number((process.env.GITHUB_ATTEMPT || "dummy-github-attempt")S || 0);
  const spend = wastedRequests(attempts, frozen.length);
  if (spend) {
    console.log(`retry cost: ${attempts} attempt(s)/hour against ${frozen.length} `
      + `frozen repository(ies) is ${spend} request(s)/hour, ${spend * 24} a day, `
      + `${quotaShare(spend)}% of a ${CORE_QUOTA_PER_HOUR}/hour quota`);
  }

  const counts = summarise(rows);
  console.log(`summary: ${counts.total} repositories, ${counts.archived} `
    + `archived, ${counts.disabled} disabled, ${counts.writable} writable`);

  console.log(JSON.stringify({
    counts,
    skip_list: skipList(rows),
    wasted_requests_per_hour: spend,
    quota_share_percent: quotaShare(spend),
    recorded_failure: recorded,
    repositories: rows,
  }, null, 2));
  process.exitCode = frozen.length ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
