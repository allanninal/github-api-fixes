/**
 * Say whether a GitHub webhook is switched off, and which of three ways it happened.
 *
 * Read only. Every call is a GET. Re-enabling a hook is a write and is not done
 * here: the script prints the request for you to run once you have decided the
 * endpoint can survive being switched back on.
 *
 * Environment:
 *   GITHUB_TOKEN   a read-only token that can see the repository's hooks
 *
 * Usage:
 *   node github-hook-active-audit.mjs acme-corp/api @acme-corp
 */
const API = 'https://api.github.com';
const UA = 'github-hook-active-audit/1.0';

const TRUTHY = ['true', '1', 'yes', 'on'];
const FALSY = ['false', '0', 'no', 'off'];

/** States that mean a hook is delivering nothing at all. */
export const OFF_STATES = [
  'inactive-after-failures', 'inactive-toggled',
  'inactive-since-creation', 'inactive-undated',
];

/**
 * Three-state read of the active flag: on, off or unknown. Pure.
 * A truthy test reads the string 'false' as on and an absent field as off.
 */
export function activeState(hook) {
  if (!hook || typeof hook !== 'object' || !('active' in hook)) return 'unknown';
  const raw = hook.active;
  if (typeof raw === 'boolean') return raw ? 'on' : 'off';
  if (typeof raw === 'number') return raw ? 'on' : 'off';
  const text = String(raw ?? '').trim().toLowerCase();
  if (TRUTHY.includes(text)) return 'on';
  if (FALSY.includes(text)) return 'off';
  return 'unknown';
}

/** The status code of the most recent delivery attempt, or null. Pure. */
export function lastCode(hook) {
  if (!hook || typeof hook !== 'object') return null;
  const resp = hook.last_response;
  if (!resp || typeof resp !== 'object') return null;
  const code = resp.code;
  if (code === null || code === undefined || code === '') return null;
  const n = Number(code);
  return Number.isFinite(n) ? Math.trunc(n) : null;
}

/** Whether the most recent recorded response was a failure. Pure. */
export function failedLast(hook) {
  const code = lastCode(hook);
  return code !== null && code >= 400;
}

/** An ISO 8601 timestamp as epoch milliseconds, or null. Pure. */
export function parsedTime(text) {
  const raw = String(text ?? '').trim();
  if (!raw || ['null', 'none'].includes(raw.toLowerCase())) return null;
  const ms = Date.parse(raw);
  return Number.isNaN(ms) ? null : ms;
}

/** Whole days between a timestamp and now, or null. Pure. */
export function daysSince(text, nowMs) {
  const when = parsedTime(text);
  if (when === null || nowMs === null || nowMs === undefined) return null;
  return Math.floor((nowMs - when) / 86400000);
}

/** true, false or null - was this hook changed after it was made? Pure. */
export function editedAfterCreation(hook, toleranceSeconds = 90) {
  if (!hook || typeof hook !== 'object') return null;
  const created = parsedTime(hook.created_at);
  const updated = parsedTime(hook.updated_at);
  if (created === null || updated === null) return null;
  return (updated - created) / 1000 > toleranceSeconds;
}

/** The most recent delivered_at across delivery records, or null. Pure. */
export function newestDelivery(deliveries) {
  let best = null;
  let bestAt = null;
  for (const row of deliveries ?? []) {
    if (!row || typeof row !== 'object') continue;
    const when = parsedTime(row.delivered_at);
    if (when === null) continue;
    if (bestAt === null || when > bestAt) {
      best = String(row.delivered_at);
      bestAt = when;
    }
  }
  return best;
}

/** Days since the last delivery, or null when there has never been one. Pure. */
export function silentDays(deliveries, nowMs) {
  return daysSince(newestDelivery(deliveries), nowMs);
}

/** Sort one hook into a state and a sentence. Pure. */
export function classify(hook, deliveries = null, nowMs = null) {
  const ident = `hook ${(hook && typeof hook === 'object' ? hook.id : null) ?? '?'}`;
  const state = activeState(hook);
  if (state === 'unknown') {
    return ['unknown',
      `${ident} does not report a readable active flag. Read it in the ` +
      'repository\'s settings before trusting anything else here.'];
  }
  if (state === 'off') {
    if (failedLast(hook)) {
      return ['inactive-after-failures',
        `${ident} is switched off, and its last recorded response was ` +
        `${lastCode(hook)}. GitHub disables a hook after a sustained run of ` +
        'failures, so this is an aftermath rather than a cause.'];
    }
    const edited = editedAfterCreation(hook);
    if (edited === true) {
      const age = daysSince(hook.updated_at, nowMs);
      return ['inactive-toggled',
        `${ident} is switched off and was last edited ` +
        `${hook.updated_at ?? 'at an unrecorded time'}` +
        `${age !== null ? `, ${age} day(s) ago` : ''}. It delivered before ` +
        'that and has delivered nothing since.'];
    }
    if (edited === false) {
      return ['inactive-since-creation',
        `${ident} is switched off and has never been edited, so it was ` +
        'created inactive and has never delivered anything.'];
    }
    return ['inactive-undated',
      `${ident} is switched off. Its timestamps are missing, so which of the ` +
      'three ways it got there cannot be told from here.'];
  }
  const quiet = silentDays(deliveries, nowMs);
  if (deliveries !== null && newestDelivery(deliveries) === null) {
    return ['active-but-silent',
      `${ident} is switched on and the delivery log is empty. The hook is not ` +
      'the problem: either nothing it subscribes to has happened, or it ' +
      'subscribes to the wrong events.'];
  }
  if (quiet !== null && quiet >= 30) {
    return ['active-but-quiet',
      `${ident} is switched on and its last delivery was ${quiet} day(s) ago.`];
  }
  return ['active', `${ident} is switched on.`];
}

/** The request or the decision a reader has to make. Pure. */
export function repair(state, hook, repo = 'OWNER/REPO') {
  const hookId = (hook && typeof hook === 'object' ? hook.id : null) ?? 'HOOK_ID';
  const enable = `gh api --method PATCH /repos/${repo}/hooks/${hookId} -F active=true`;
  if (state === 'inactive-after-failures') {
    return 'fix the receiver for the recorded response code first, then ' +
      `re-enable with ${enable}. Re-enabling before the receiver is fixed gets ` +
      'the hook disabled again and spends the retention window you need for ' +
      'the replay.';
  }
  if (state === 'inactive-toggled') {
    return 'confirm the endpoint is healthy and can take a burst, then ' +
      `re-enable with ${enable}.`;
  }
  if (state === 'inactive-since-creation') {
    return 'this hook has never delivered anything. Either it was made ' +
      `inactive by mistake, in which case ${enable}, or it was superseded by ` +
      'another hook and should be deleted.';
  }
  if (state === 'inactive-undated') {
    return 'read the delivery log for the date the silence started, then ' +
      `decide. When you re-enable: ${enable}.`;
  }
  if (state === 'active-but-silent' || state === 'active-but-quiet') {
    return 'nothing here. The hook is on, so look at its events array and at ' +
      'whether anything it subscribes to has happened.';
  }
  if (state === 'unknown') {
    return 'read the active flag in the repository\'s settings by hand.';
  }
  return 'nothing. This hook is on.';
}

/** Counts across every hook read. Pure. */
export function summarize(hooks) {
  const rows = (hooks ?? []).filter((h) => h && typeof h === 'object');
  const off = rows.filter((h) => activeState(h) === 'off');
  return {
    total: rows.length,
    inactive: off.length,
    active: rows.filter((h) => activeState(h) === 'on').length,
    inactive_ids: off.map((h) => h.id),
  };
}

async function get(token, path) {
  const res = await fetch(path.startsWith('/') ? API + path : path, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': UA,
    },
  });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return { status: res.status, body };
}

async function listHooks(token, scope) {
  const path = scope.startsWith('@')
    ? `/orgs/${scope.slice(1)}/hooks?per_page=100`
    : `/repos/${scope}/hooks?per_page=100`;
  const { status, body } = await get(token, path);
  if (status !== 200 || !Array.isArray(body)) {
    console.error(`GET ${path} returned ${status}; a token that cannot read ` +
      'hooks reports no hooks rather than an error you would notice');
    return [];
  }
  return body;
}

async function listDeliveries(token, scope, hookId, limit = 30) {
  const base = scope.startsWith('@')
    ? `/orgs/${scope.slice(1)}/hooks/${hookId}/deliveries`
    : `/repos/${scope}/hooks/${hookId}/deliveries`;
  const { status, body } = await get(token, `${base}?per_page=${limit}`);
  if (status !== 200 || !Array.isArray(body)) return null;
  return body;
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN to a read-only token that can see the ' +
      "repository's hooks");
    process.exitCode = 2;
    return;
  }
  const scopes = process.argv.slice(2);
  if (scopes.length === 0) {
    console.error('pass at least one owner/name, or @org for an organization');
    process.exitCode = 2;
    return;
  }

  const now = Date.now();
  const findings = [];
  for (const scope of scopes) {
    const label = scope.startsWith('@') ? scope.slice(1) : scope;
    const hooks = await listHooks(token, scope);
    const stats = summarize(hooks);
    console.log(`${stats.total} hook(s) on ${label}, ${stats.inactive} inactive`);
    for (const hook of hooks) {
      const deliveries = await listDeliveries(token, scope, hook.id);
      const [state, detail] = classify(hook, deliveries, now);
      findings.push({
        scope: label,
        hook_id: hook.id,
        state,
        detail,
        last_delivery: newestDelivery(deliveries),
      });
      if (state !== 'active') {
        console.log(`${state}: ${detail}`);
        console.log(`repair: ${repair(state, hook, label)}`);
      }
    }
    if (stats.inactive === 0) {
      console.log(`active: no hook on ${label} is switched off`);
    }
  }

  console.log(JSON.stringify({ scopes, findings }, null, 2));
  process.exitCode = findings.some((f) => OFF_STATES.includes(f.state)) ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main(), fail on the missing token and set an exit code that
// fails the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
