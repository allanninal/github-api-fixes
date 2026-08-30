/**
 * Find accounts where a GitHub App was requested and never approved.
 *
 * Read only. GETs against the App's own records with the App JWT, plus a local
 * JSON file of your own connection state. This script never requests an
 * installation and never approves one: approving belongs to an organization
 * owner. It detects the state and prints the step.
 *
 * Only an owner can install an App on an organization. Anybody else going
 * through the flow creates a request that waits in a queue, and until it is
 * approved the App has no installation on that account at all.
 *
 * The API does not list pending requests, so an absent account is pending,
 * declined, abandoned or never attempted with the same silence. Your own
 * record of who began a flow is what separates them.
 *
 * Environment:
 *   GITHUB_APP_JWT    the JWT your own signing code produced
 *   GITHUB_RECORD     path to a JSON list of {account, started_at, connected}
 *   GITHUB_ACCOUNTS   comma-separated accounts you believe are connected
 */
import { readFileSync } from 'node:fs';

const API = 'https://api.github.com';
const UA = 'github-app-installation-pending/1.0';

/** How long a request can sit before it is more likely forgotten than pending. */
export const STALE_AFTER_DAYS = 7;

/** The honest core of the note, said once. */
export const ABSENCE_MEANING = 'absence covers pending, declined and never '
  + 'started; the API publishes no request queue, so your record is what makes '
  + 'this readable.';

/** Requests this run will spend against the core quota. Pure. */
export function readCost(accounts, pages = 1) {
  return 1 + Math.max(1, Number(pages) || 1) + (accounts ? accounts.length : 0);
}

/** Index the App's installations by account login. Pure. */
export function installationIndex(installations) {
  const index = {};
  for (const item of installations || []) {
    if (!item || typeof item !== 'object') continue;
    const account = item.account && typeof item.account === 'object' ? item.account : {};
    const login = account.login;
    if (!login) continue;
    index[String(login).trim().toLowerCase()] = {
      id: item.id,
      created_at: item.created_at,
      repository_selection: item.repository_selection,
      suspended: !['', null, undefined, 'null'].includes(item.suspended_at),
    };
  }
  return index;
}

/** What GET /orgs/{org}/installation means. Pure. [state, detail]. */
export function probeState(status) {
  const code = Number(status) || 0;
  if (code === 200) return ['installed', 'the App has an installation on this account.'];
  if (code === 404) {
    return ['no-installation', `the App has no installation on this account. ${ABSENCE_MEANING}`];
  }
  if ([401, 403].includes(code)) {
    return ['unreadable', 'the JWT was refused on this probe, so nothing can '
      + 'be concluded about the account.'];
  }
  return ['unclear', `HTTP ${status} is not one of the answers this probe gives.`];
}

/** An ISO 8601 timestamp as a Date, or null. Pure. */
export function parsedTime(text) {
  if (!text) return null;
  const ms = Date.parse(String(text));
  return Number.isNaN(ms) ? null : new Date(ms);
}

/** How long ago the flow started, in days. Pure. null if unparseable. */
export function ageDays(startedAt, now) {
  const start = parsedTime(startedAt);
  if (start === null || !now) return null;
  return (now.getTime() - start.getTime()) / 86400000;
}

/** Is this request plausibly still in flight. Pure. [state, detail]. */
export function requestAgeState(days, staleAfter = STALE_AFTER_DAYS) {
  if (days === null || days === undefined) {
    return ['age-unknown', 'your record does not say when the flow started, so '
      + 'the request cannot be aged.'];
  }
  if (days <= staleAfter) {
    return ['awaiting-approval', `the flow started ${days.toFixed(1)} day(s) `
      + 'ago, which is recent enough that an owner may simply not have looked '
      + 'yet.'];
  }
  return ['stale-request', `the flow started ${days.toFixed(1)} day(s) ago. A `
    + 'request that old is more likely forgotten than pending, and the owner '
    + 'who could approve it was notified once.'];
}

/** One account, two sources of truth. Pure. [state, detail]. */
export function reconcile(entry, installation, now, staleAfter = STALE_AFTER_DAYS) {
  const record = entry || {};
  const account = String(record.account ?? 'unknown');
  const connected = Boolean(record.connected);
  const startedAt = record.started_at;

  if (installation) {
    if (installation.suspended) {
      return ['installed-but-suspended', `an installation exists on ${account} `
        + 'and is suspended, which is a different diagnosis and a different '
        + 'repair. Do not chase an approval that already happened.'];
    }
    if (connected) {
      return ['agreed-connected', 'an installation exists and your record '
        + 'agrees. Nothing to reconcile.'];
    }
    return ['unrecorded-installation', `an installation exists on ${account} `
      + 'and your record does not show it as connected. An owner approved it '
      + 'after the fact and nothing in your product noticed.'];
  }
  if (connected) {
    return ['false-connected', `your record says connected`
      + `${startedAt ? ` since ${startedAt}` : ''} and this App has no `
      + `installation on ${account}. ${ABSENCE_MEANING}`];
  }
  const [ageState, ageDetail] = requestAgeState(ageDays(startedAt, now), staleAfter);
  if (['awaiting-approval', 'stale-request'].includes(ageState)) {
    return [ageState, `${ageDetail} ${ABSENCE_MEANING}`];
  }
  return ['agreed-disconnected', 'no installation, and your record does not '
    + 'claim one. There is nothing here to explain.'];
}

/** Is this a state somebody has to do something about. Pure. */
export function actionable(state) {
  return ['false-connected', 'awaiting-approval', 'stale-request',
    'unrecorded-installation', 'installed-but-suspended'].includes(state);
}

/** The step to put in front of a human. Pure. Nothing here is executed. */
export function printedStep(state, account) {
  if (['false-connected', 'awaiting-approval', 'stale-request'].includes(state)) {
    return `an owner of ${account} has to approve the pending installation `
      + "request from the organization's GitHub Apps settings. Nothing here "
      + 'requests or approves anything.';
  }
  if (state === 'unrecorded-installation') {
    return `reconcile your stored connection state for ${account}: the `
      + 'installation is real and your product is ignoring it.';
  }
  if (state === 'installed-but-suspended') {
    return `ask an owner of ${account} to unsuspend the installation. The `
      + 'approval is not what is missing.';
  }
  return 'nothing for this account.';
}

/** What to change in the product, given everything seen. Pure. */
export function productRepair(states) {
  const seen = states || [];
  if (seen.includes('false-connected')) {
    return 'stop rendering a completed flow as a connection. Show the requested '
      + 'state explicitly, prompt the user to ask an owner to approve it, and '
      + 'reconcile against GET /app/installations on a schedule rather than '
      + 'trusting the callback.';
  }
  if (seen.some((s) => ['awaiting-approval', 'stale-request'].includes(s))) {
    return 'surface the pending state in the product and re-check it on a '
      + 'schedule. A request that nobody is reminded about is a request that '
      + 'expires by neglect.';
  }
  if (seen.includes('unrecorded-installation')) {
    return 'reconcile in the other direction too: an installation approved '
      + 'after the user gave up delivers nothing if your product never records '
      + 'it.';
  }
  return "nothing. The App's installations and your record agree.";
}

function headers(jwt) {
  return {
    Authorization: `Bearer ${jwt}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
}

async function main() {
  const jwt = (process.env.GITHUB_APP_JWT || "dummy-github-app-jwt");
  if (!jwt) {
    console.error('set GITHUB_APP_JWT (the JWT your own signing code produced)');
    process.exitCode = 2;
    return;
  }
  const entries = [];
  if ((process.env.GITHUB_RECORD || "dummy-github-record")) {
    const loaded = JSON.parse(readFileSync((process.env.GITHUB_RECORD || "dummy-github-record"), 'utf-8'));
    for (const item of Array.isArray(loaded) ? loaded : []) {
      if (item && item.account) entries.push(item);
    }
  }
  for (const account of ((process.env.GITHUB_ACCOUNT || "dummy-github-account")S || '').split(',')
    .map((s) => s.trim()).filter(Boolean)) {
    entries.push({ account, connected: true });
  }
  if (entries.length === 0) {
    console.error('nothing to reconcile: set GITHUB_RECORD or GITHUB_ACCOUNTS. '
      + "This script compares GitHub's list against your own, and the second "
      + 'half is the half GitHub cannot supply.');
    process.exitCode = 2;
    return;
  }

  const maxPages = Number((process.env.GITHUB_MAX_PAGE || "dummy-github-max-page")S || 5) || 5;
  console.log(`read cost: up to ${readCost(entries, maxPages)} request(s) against `
    + 'the core quota');

  const app = await fetch(`${API}/app`, { headers: headers(jwt) });
  if (app.status !== 200) {
    console.error(`GET /app returned HTTP ${app.status}: the JWT was not accepted`);
    process.exitCode = 2;
    return;
  }
  console.log(`app: ${(await app.json()).slug} (JWT accepted)`);

  const installations = [];
  let pages = 0;
  for (let page = 1; page <= Math.max(1, maxPages); page += 1) {
    const response = await fetch(`${API}/app/installations?per_page=100&page=${page}`,
      { headers: headers(jwt) });
    if (response.status !== 200) {
      console.warn(`installation list page ${page} returned HTTP ${response.status}; `
        + 'the list below is partial');
      break;
    }
    const batch = await response.json();
    pages = page;
    installations.push(...batch);
    if (batch.length < 100) break;
  }
  console.log(`installations: ${installations.length} read from ${pages} page(s)`);

  const index = installationIndex(installations);
  const now = new Date();
  const results = [];
  const states = [];

  for (const entry of entries) {
    const account = String(entry.account);
    // eslint-disable-next-line no-await-in-loop
    const probe = await fetch(`${API}/orgs/${account}/installation`,
      { headers: headers(jwt) });
    const [probeResult] = probeState(probe.status);
    let installation = index[account.trim().toLowerCase()] || null;
    if (probeResult === 'no-installation') installation = null;
    const [state, detail] = reconcile(entry, installation, now);
    console.log(`${account}: HTTP ${probe.status} from GET /orgs/${account}/installation`);
    console.log(`  ${state} — ${detail}`);
    console.log(`  step: ${printedStep(state, account)}`);
    states.push(state);
    results.push({
      account,
      probe_status: probe.status,
      probe_state: probeResult,
      installation_id: installation ? installation.id : null,
      state,
      detail,
      actionable: actionable(state),
      step: printedStep(state, account),
    });
  }

  console.log(`product repair: ${productRepair(states)}`);
  console.log(JSON.stringify({
    installations_read: installations.length,
    pages_read: pages,
    accounts: results,
    product_repair: productRepair(states),
  }, null, 2));
  process.exitCode = states.some((s) => actionable(s)) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
