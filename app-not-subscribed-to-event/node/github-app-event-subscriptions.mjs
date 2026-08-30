/**
 * Find webhook events a GitHub App's handlers wait for and never receive.
 *
 * Read only. Two GETs with the App's JWT: the App's own record and its recent
 * webhook deliveries. Subscribing is an edit to the App and then a human
 * acceptance on every installation, so the script prints the three steps in
 * the order they have to happen.
 *
 * This is the App-side case. A repository or organization webhook has its own
 * events list, which is a different object with a different repair.
 *
 * Environment:
 *   GITHUB_APP_JWT   the JWT your own signing code produced
 */
const API = 'https://api.github.com';
const UA = 'github-app-event-subscriptions/1.0';

/** Which App permission gates which event. Curated, not fetched. */
export const EVENT_PERMISSION = {
  check_run: 'checks',
  check_suite: 'checks',
  commit_comment: 'contents',
  create: 'contents',
  delete: 'contents',
  deployment: 'deployments',
  deployment_status: 'deployments',
  fork: 'metadata',
  issue_comment: 'issues',
  issues: 'issues',
  label: 'metadata',
  member: 'members',
  membership: 'members',
  milestone: 'issues',
  organization: 'members',
  public: 'metadata',
  pull_request: 'pull_requests',
  pull_request_review: 'pull_requests',
  pull_request_review_comment: 'pull_requests',
  pull_request_review_thread: 'pull_requests',
  push: 'contents',
  release: 'contents',
  repository: 'metadata',
  repository_dispatch: 'contents',
  star: 'metadata',
  status: 'statuses',
  team_add: 'members',
  watch: 'metadata',
  workflow_dispatch: 'actions',
  workflow_job: 'actions',
  workflow_run: 'actions',
};

/** Permissions every App holds implicitly. */
export const ALWAYS_HELD = ['metadata'];

/** An event name reduced to the form GitHub spells it in. Pure. */
export function normalize(event) {
  return String(event ?? '').trim().toLowerCase();
}

/** The App permission that gates an event, or null if unknown. Pure. */
export function gatingPermission(event) {
  return EVENT_PERMISSION[normalize(event)] ?? null;
}

/** Whether the App holds a permission at read or better. Pure. */
export function holds(permissions, name) {
  if (ALWAYS_HELD.includes(name)) return true;
  const value = (permissions ?? {})[name];
  return Boolean(value) && String(value).trim().toLowerCase() !== 'none';
}

/** Distinct event names in a delivery log page. Pure. */
export function seenEvents(deliveries) {
  const out = new Set();
  for (const row of deliveries ?? []) {
    if (row && typeof row === 'object' && row.event) out.add(normalize(row.event));
  }
  return out;
}

/** Sort one handled event into a state. Pure. */
export function subscriptionState(event, subscribed, permissions, seen = null) {
  const name = normalize(event);
  const declared = new Set((subscribed ?? []).map(normalize));
  const gate = gatingPermission(name);
  if (declared.has(name)) {
    if (seen && seen.has(name)) {
      return ['subscribed-and-arriving',
        `${name} is declared by the App and has arrived recently.`];
    }
    return ['subscribed-not-yet-seen',
      `${name} is declared by the App but has not arrived in the retention ` +
      'window, which usually means it has not happened rather than that it ' +
      'is broken.'];
  }
  if (gate === null) {
    return ['not-subscribed-gate-unknown',
      `${name} is not declared by the App, so it has never been delivered. ` +
      'This script does not know which permission gates it; check the ' +
      'published event list before requesting one.'];
  }
  if (!holds(permissions, gate)) {
    return ['not-subscribed-blocked',
      `${name} is not declared, and the ${gate} permission that gates it is ` +
      'not held. The subscription cannot be ticked until the permission is added.'];
  }
  return ['not-subscribed-permitted',
    `${name} is not declared, but the ${gate} permission that gates it is ` +
    'held, so subscribing is an edit to the App followed by an acceptance round.'];
}

/** One row per handled event, in the order they were given. Pure. */
export function rows(handled, subscribed, permissions, seen = null) {
  return (handled ?? []).map((event) => {
    const [state, detail] = subscriptionState(event, subscribed, permissions, seen);
    return { event: normalize(event), state, detail, gated_by: gatingPermission(event) };
  });
}

/** Turn the rows into one finding. Pure. */
export function verdict(report) {
  const all = report ?? [];
  if (!all.length) {
    return ['nothing-handled',
      'no handled events were supplied, so there is nothing to compare the ' +
      "App's subscriptions against."];
  }
  const unreachable = all.filter((r) => r.state.startsWith('not-subscribed'));
  if (unreachable.length) {
    return ['handlers-unreachable',
      `${unreachable.length} of ${all.length} handled event(s) can never ` +
      'fire, because the App does not declare them.'];
  }
  const quiet = all.filter((r) => r.state === 'subscribed-not-yet-seen');
  if (quiet.length) {
    return ['all-subscribed-some-quiet',
      `every handled event is declared. ${quiet.length} of them has not ` +
      'arrived in the retention window, which is not by itself a fault.'];
  }
  return ['all-subscribed',
    'every handled event is declared by the App and arriving.'];
}

/** The ordered repair, as lines. Pure. */
export function repairSteps(report) {
  const all = report ?? [];
  const blocked = [...new Set(all
    .filter((r) => r.state === 'not-subscribed-blocked' && r.gated_by)
    .map((r) => r.gated_by))].sort();
  const missing = [...new Set(all
    .filter((r) => r.state.startsWith('not-subscribed'))
    .map((r) => r.event))].sort();
  if (!missing.length) return [];
  const steps = [];
  if (blocked.length) {
    steps.push(`add the ${blocked.join(', ')} permission to the App; until ` +
      'then the subscription cannot be selected at all');
  }
  steps.push(`subscribe the App to ${missing.join(', ')}`);
  steps.push('have an owner on every installation accept the resulting ' +
    'permission request, or the event will arrive from some accounts and not others');
  return steps;
}

async function get(jwt, path) {
  const url = path.startsWith('/') ? API + path : path;
  const res = await fetch(url, {
    headers: {
      Authorization: `Bearer ${jwt}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': UA,
    },
  });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return { status: res.status, body };
}

async function main() {
  const jwt = (process.env.GITHUB_APP_JWT || "dummy-github-app-jwt");
  if (!jwt) {
    console.error('set GITHUB_APP_JWT to the JWT your own signing code ' +
      "produced. The App's events array is on the App record, which an " +
      'installation token cannot read');
    process.exitCode = 2;
    return;
  }
  const handled = (process.argv[2] ?? '').split(',').map((s) => s.trim()).filter(Boolean);
  if (!handled.length) {
    console.error('pass the event names your receiver implements as the first ' +
      'argument, comma separated; without them there is nothing to compare');
    process.exitCode = 2;
    return;
  }

  const app = await get(jwt, '/app');
  if (app.status !== 200 || !app.body || typeof app.body !== 'object') {
    console.error(`GET /app returned ${app.status}, so the App's ` +
      'subscriptions cannot be read');
    process.exitCode = 2;
    return;
  }
  const subscribed = app.body.events ?? [];
  const permissions = app.body.permissions ?? {};
  console.log(`app subscribes to ${subscribed.length} event(s), holds ` +
    `${Object.keys(permissions).length} permission(s)`);

  let seen = null;
  const log = await get(jwt, '/app/hook/deliveries?per_page=100');
  if (log.status === 200 && Array.isArray(log.body)) {
    seen = seenEvents(log.body);
    console.log(`delivery log shows ${seen.size} distinct event(s) in the ` +
      'retention window');
  } else {
    console.log(`delivery log unavailable (${log.status}); the subscription ` +
      'answer does not depend on it');
  }

  const report = rows(handled, subscribed, permissions, seen);
  const [state, detail] = verdict(report);
  console.log(`${state}: ${detail}`);
  for (const row of report) {
    if (row.state !== 'subscribed-and-arriving') console.log(`  ${row.detail}`);
  }
  repairSteps(report).forEach((line, i) => {
    console.log(`repair step ${i + 1}: ${line}`);
  });

  console.log(JSON.stringify({
    subscribed: subscribed.map(normalize).sort(),
    permissions,
    seen_in_deliveries: seen ? [...seen].sort() : null,
    state,
    events: report,
  }, null, 2));
  process.exitCode = state === 'handlers-unreachable' ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main(), fail on the missing JWT and set an exit code that
// fails the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
