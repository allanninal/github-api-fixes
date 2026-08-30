/**
 * Say how long a webhook secret has gone without being rotated.
 *
 * Read only. One GET per scope. Nothing is changed, and no secret value is ever
 * read, held or printed: presence is the only readable fact and the only one
 * this program reports.
 *
 * updated_at is the only clock the API offers, and it moves on any edit, which
 * makes it conclusive in one direction. An old timestamp proves no rotation. A
 * recent one proves an edit and nothing else.
 *
 * Environment:
 *   GITHUB_TOKEN        a read-only token
 *   GITHUB_REPO         owner/name
 *   GITHUB_ORG          optional, audited as well where readable
 *   GITHUB_ROTATED_ON   optional, the date your records claim, as YYYY-MM-DD
 */
const API = 'https://api.github.com';
const UA = 'github-hook-secret-age/1.0';

/** A policy number, not a published one. The value is in having chosen it. */
export const DEFAULT_MAX_AGE_DAYS = 180;
export const UNEDITED_TOLERANCE_SECONDS = 60;

/** Whether a secret is configured. Never returns the value. Pure. */
export function secretState(config) {
  if (!config || typeof config !== 'object') return 'unknown';
  return config.secret !== null && config.secret !== undefined ? 'set' : 'absent';
}

/** A copy of a hook config that is safe to print. Pure. */
export function redact(config) {
  if (!config || typeof config !== 'object') return {};
  const safe = {};
  for (const [key, value] of Object.entries(config)) {
    if (key !== 'secret') safe[key] = value;
  }
  safe.secret = secretState(config);
  return safe;
}

/** An ISO 8601 timestamp as a Date, or null. Pure. */
export function parseTime(text) {
  const raw = String(text ?? '').trim();
  if (!raw) return null;
  const moment = new Date(raw);
  return Number.isNaN(moment.getTime()) ? null : moment;
}

/** Whole days between a timestamp and now, or null. Pure. */
export function ageDays(text, now) {
  const moment = parseTime(text);
  const at = parseTime(now) || (now instanceof Date ? now : null);
  if (moment === null || at === null) return null;
  return Math.floor((at.getTime() - moment.getTime()) / 86400000);
}

/** Whether the hook is exactly as it was created. Pure. */
export function uneditedSinceCreation(createdAt, updatedAt) {
  const created = parseTime(createdAt);
  const updated = parseTime(updatedAt);
  if (created === null || updated === null) return false;
  return Math.abs(updated.getTime() - created.getTime()) <= UNEDITED_TOLERANCE_SECONDS * 1000;
}

/** What the age of the last edit actually proves. Pure. */
export function evidenceDirection(age, threshold) {
  if (age === null || age === undefined) return 'unknown';
  return age >= Number(threshold) ? 'conclusive' : 'inconclusive';
}

/** Compare a claimed rotation date against the hook's own timestamp. Pure. */
export function reconcile(updatedAt, claimed) {
  const claim = parseTime(claimed);
  const updated = parseTime(updatedAt);
  if (claim === null || updated === null) return 'unknown';
  return updated.getTime() < claim.getTime() ? 'not-applied' : 'consistent';
}

/** Turn presence, age and any claimed rotation into a finding. Pure. */
export function verdict(config, createdAt, updatedAt, now,
                        threshold = DEFAULT_MAX_AGE_DAYS, claimed = null) {
  if (secretState(config) !== 'set') {
    return ['no-secret',
      'this hook has no secret at all, so there is nothing to rotate and every '
      + 'delivery arrives unsigned. That is a different and larger finding than this one.'];
  }
  const age = ageDays(updatedAt, now);
  if (age === null) {
    return ['age-unknown',
      'a secret is set, but updated_at could not be read, so nothing about its '
      + 'age can be established from here.'];
  }
  if (claimed && reconcile(updatedAt, claimed) === 'not-applied') {
    return ['rotation-not-applied',
      `the record claims a rotation on ${String(claimed).slice(0, 10)}, but the `
      + `hook has not been edited since ${String(updatedAt).slice(0, 10)}. Changing `
      + 'a secret is an edit, so whatever was rotated, it was not this hook.'];
  }
  const origin = uneditedSinceCreation(createdAt, updatedAt)
    ? 'created_at and updated_at agree, so this is the secret the hook was created with.'
    : 'the hook has been edited since it was created, though not necessarily its secret.';
  if (evidenceDirection(age, threshold) === 'conclusive') {
    return ['overdue',
      `the hook has not been edited for ${age} days, so its secret has not been `
      + `rotated for at least that long. ${origin}`];
  }
  return ['inconclusive',
    `the hook was edited ${age} days ago, which is inside the rotation interval, `
    + 'but an edit is not a rotation: updated_at moves for a URL change too. '
    + 'This is unknown rather than compliant.'];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (['overdue', 'rotation-not-applied'].includes(state)) {
    return 'rotate with an overlap window: teach the receiver to accept a '
      + 'signature from the old or the new secret, deploy that, change the secret '
      + 'on GitHub, then drop the old value once deliveries have settled. A '
      + 'straight swap loses whatever is in flight.';
  }
  if (state === 'inconclusive') {
    return 'record rotations somewhere the next person can read, and run this '
      + 'again with that date. The API cannot date a secret, so a written record '
      + 'is the only thing that turns this into an answer.';
  }
  if (state === 'no-secret') {
    return 'set a secret on the hook and verify X-Hub-Signature-256 in the '
      + 'receiver. Age is not the problem here.';
  }
  if (state === 'age-unknown') return 'read created_at and updated_at on the hook by hand.';
  return 'nothing.';
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
  const org = (process.env.GITHUB_ORG || "dummy-github-org");
  if (!token || (!repo && !org)) {
    console.error('set GITHUB_TOKEN and at least one of GITHUB_REPO, GITHUB_ORG');
    process.exitCode = 2;
    return;
  }
  const threshold = Number((process.env.GITHUB_MAX_AGE_DAY || "dummy-github-max-age-day")S || DEFAULT_MAX_AGE_DAYS);
  const claimed = (process.env.GITHUB_ROTATED_O || "dummy-github-rotated-o")N || null;
  const now = new Date();

  const scopes = [];
  if (repo) scopes.push([`/repos/${repo}/hooks?per_page=100`, repo]);
  if (org) scopes.push([`/orgs/${org}/hooks?per_page=100`, org]);

  const report = [];
  let findings = 0;
  for (const [path, label] of scopes) {
    const res = await fetch(API + path, { headers: headers(token) });
    if (res.status !== 200) {
      console.log(`GET ${path} returned ${res.status}; ${label} hooks are not readable`);
      continue;
    }
    const hooks = await res.json();
    for (const hook of Array.isArray(hooks) ? hooks : []) {
      const config = hook.config || {};
      const safe = redact(config);
      console.log(`hook ${hook.id} ${safe.url} secret=${safe.secret}`);
      const [state, detail] = verdict(config, hook.created_at, hook.updated_at,
        now, threshold, claimed);
      console.log(`${state}: ${detail}`);
      console.log(`repair: ${repair(state)}`);
      if (['overdue', 'rotation-not-applied', 'no-secret'].includes(state)) findings += 1;
      report.push({
        scope: label,
        hook_id: hook.id,
        config: safe,
        created_at: hook.created_at,
        updated_at: hook.updated_at,
        days_since_edit: ageDays(hook.updated_at, now),
        unedited_since_creation: uneditedSinceCreation(hook.created_at, hook.updated_at),
        evidence: evidenceDirection(ageDays(hook.updated_at, now), threshold),
        rotation_record: reconcile(hook.updated_at, claimed),
        state,
      });
    }
  }
  console.log(JSON.stringify({ rotation_interval_days: threshold, hooks: report }, null, 2));
  process.exitCode = findings ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
