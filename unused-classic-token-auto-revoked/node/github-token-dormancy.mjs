/**
 * Say which stored credentials will be reaped for disuse before they are needed.
 *
 * Read only, and free: one GET /rate_limit per credential, which consumes no
 * quota and requires no scope. That call is also the mitigation, because it
 * counts as a use of the credential.
 *
 * GitHub removes classic personal access tokens that have gone a year without
 * being used. That class carries no expiry and emits no header, so the clock
 * comes from the manifest rather than from the API.
 */
import { readFileSync } from 'node:fs';

const API = 'https://api.github.com';
const UA = 'github-token-dormancy/1.0 (+https://example.com/contact)';

export const WINDOW_DAYS = 365;
export const TIGHT_DAYS = 60;

/** Name the credential class from its prefix. Pure. Never returns the value. */
export function tokenClass(value) {
  if (value === null || value === undefined || !String(value).trim()) return 'absent';
  const text = String(value).trim();
  if (text.startsWith('github_pat_')) return 'fine-grained';
  if (text.startsWith('ghp_')) return 'classic';
  if (text.startsWith('ghs_')) return 'installation';
  if (text.startsWith('gho_') || text.startsWith('ghu_')) return 'oauth';
  if (text.length === 40 && /^[0-9a-f]+$/.test(text.toLowerCase())) return 'classic';
  return 'unknown';
}

/** Decide whether a credential is in the class the reaper can take. Pure. */
export function reapExposure(kind, expiresHeader) {
  if (expiresHeader) {
    return ['not-reapable-expiring',
      'this credential reports an expiry, so it dies on a date rather than ' +
      'from disuse. The countdown on that date is a different check.'];
  }
  if (kind === 'classic') {
    return ['reapable',
      'a classic token with no expiry reported. This is the only class ' +
      'GitHub removes for disuse, and it emits no header to warn you.'];
  }
  if (kind === 'fine-grained') {
    return ['not-reapable-fine-grained',
      'fine-grained tokens carry an expiry by default, so they are governed ' +
      'by a date even when this request did not show one.'];
  }
  if (kind === 'installation') {
    return ['not-reapable-short-lived',
      'an installation access token lives about an hour. It is minted per ' +
      'run and dormancy is meaningless for it.'];
  }
  if (kind === 'oauth') {
    return ['not-reapable-oauth',
      'an OAuth user token dies when somebody revokes the authorization, ' +
      'which is a decision rather than a clock.'];
  }
  return ['unknown-class',
    'the credential does not match a known prefix, so its class cannot be ' +
    'named from its text. Treat it as reapable until somebody confirms ' +
    'otherwise.'];
}

/** Days of headroom between one use and the reaping window. Pure. */
export function marginDays(intervalDays, windowDays = WINDOW_DAYS) {
  const interval = Number(intervalDays);
  if (intervalDays === null || intervalDays === undefined
      || intervalDays === '' || Number.isNaN(interval)) return null;
  return windowDays - interval;
}

/** Turn a probe result and an exercise cadence into a finding. Pure. */
export function dormancyState(probeStatus, exposure, intervalDays,
  windowDays = WINDOW_DAYS, tightDays = TIGHT_DAYS) {
  if (probeStatus === 401) {
    return ['already-gone',
      'the credential is refused. For this class there is nothing to ' +
      'un-revoke: mint a replacement and record what it is for.'];
  }
  if (probeStatus === null || probeStatus === undefined || probeStatus >= 400) {
    return ['unreachable',
      'the probe did not come back cleanly, so nothing can be said about ' +
      'the credential yet. Fix the probe first.'];
  }
  if (exposure !== 'reapable' && exposure !== 'unknown-class') {
    return ['not-reapable',
      'alive, and not in the class that gets reaped for disuse.'];
  }
  const margin = marginDays(intervalDays, windowDays);
  if (margin === null) {
    return ['cadence-unknown',
      'alive, and reapable, but the manifest does not say how often anything ' +
      'exercises it. That is the number this check needs.'];
  }
  if (margin <= 0) {
    return ['reap-race-lost',
      'alive today, and nothing exercises it inside the window. This ' +
      'credential will be removed before it is next needed.'];
  }
  if (margin < tightDays) {
    return ['reap-race-tight',
      'alive, with less headroom than one skipped run. A paused pipeline or ' +
      'a quiet quarter loses this race.'];
  }
  return ['covered',
    'alive, and exercised often enough that the job itself keeps the ' +
    'credential from going dormant.'];
}

/** Recommend a keep-alive cadence in days. Pure. */
export function probeInterval(intervalDays, windowDays = WINDOW_DAYS) {
  let interval = Number(intervalDays);
  if (intervalDays === null || intervalDays === undefined
      || intervalDays === '' || Number.isNaN(interval)) interval = windowDays;
  return Math.trunc(Math.max(1, Math.min(30, interval)));
}

/** A crontab line for a keep-alive at the given cadence. Pure. */
export function keepaliveCron(days) {
  if (days <= 1) return '0 6 * * *';
  if (days <= 7) return '0 6 * * 1';
  return '0 6 1 * *';
}

async function probe(token) {
  const res = await fetch(`${API}/rate_limit`, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': UA,
    },
  });
  return [res.status, res.headers.get('github-authentication-token-expiration')];
}

function arg(name, fallback) {
  const i = process.argv.indexOf(name);
  return i === -1 ? fallback : process.argv[i + 1];
}

async function main() {
  const manifestPath = arg('--manifest', null);
  const entries = manifestPath
    ? JSON.parse(readFileSync(manifestPath, 'utf8'))
    : [{ env: 'GITHUB_TOKEN', label: 'the credential in GITHUB_TOKEN', exercised_every_days: null }];

  const findings = [];
  for (const entry of entries) {
    const name = entry.env ?? '';
    const label = entry.label ?? name;
    const cadence = entry.exercised_every_days ?? null;
    const token = process.env[name];
    if (!token) {
      console.warn(`${name.padEnd(20)} ${String(label).padEnd(24)} no value in the environment`);
      findings.push({ env: name, state: 'not-set' });
      continue;
    }

    const kind = tokenClass(token);
    const [status, expires] = await probe(token);
    const [exposure, exposureDetail] = reapExposure(kind, expires);
    const [state, detail] = dormancyState(status, exposure, cadence);
    const margin = marginDays(cadence);
    console.log(`${name.padEnd(20)} ${String(label).padEnd(24)} ${state.padEnd(16)} `
      + `margin ${margin === null ? 'unknown' : `${margin}d`}`);
    console.log(`    class ${kind}: ${exposureDetail}`);
    console.log(`    ${detail}`);

    if (state === 'reap-race-lost' || state === 'reap-race-tight' || state === 'cadence-unknown') {
      const every = probeInterval(cadence);
      console.log(`    repair: probe this credential every ${every} days. `
        + 'GET /rate_limit costs no quota, needs no scope, and counts as a '
        + 'use, so the probe is the fix.');
      console.log(`    crontab: ${keepaliveCron(every)}`);
      console.log('    repair: schedule it separately from the job that owns '
        + 'the credential. A check inside an annual job runs annually, which '
        + 'is the interval that caused this.');
    }
    if (state === 'already-gone') {
      console.log('    repair: mint a replacement, then record its purpose and '
        + 'owner somewhere the next drill will find them.');
    }

    findings.push({ env: name, label, class: kind, exposure, status, marginDays: margin, state });
  }

  console.log(JSON.stringify(findings, null, 2));
  const bad = ['reap-race-lost', 'reap-race-tight', 'already-gone'];
  process.exitCode = findings.some((f) => bad.includes(f.state)) ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not fire live requests and set an exit code the suite then inherits.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
