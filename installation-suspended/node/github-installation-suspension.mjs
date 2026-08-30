/**
 * Say whether a GitHub App installation is suspended, and stop retrying if so.
 *
 * Read only. GETs against the App's own installation records with the App JWT,
 * plus one optional probe with an installation access token. The endpoint that
 * unsuspends an installation is a write and is not called here; the script
 * prints the request you have to make of an organization owner instead.
 *
 * Environment:
 *   GITHUB_APP_JWT             the JWT your own signing code produced
 *   GITHUB_INSTALLATION_TOKEN  optional, used only to corroborate
 *   GITHUB_INSTALLATION_ID     optional, the installation to ask about
 */
const API = 'https://api.github.com';
const UA = 'github-installation-suspension/1.0';

/** States no amount of waiting will clear. */
export const TERMINAL = ['suspended', 'not-listed'];

/**
 * The suspension timestamp on an installation record, or null. Pure.
 * Absent arrives as a missing key, null, an empty string or the four
 * characters n-u-l-l depending on what deserialised the JSON.
 */
export function suspendedAt(inst) {
  if (!inst || typeof inst !== 'object') return null;
  const raw = inst.suspended_at;
  if (raw === null || raw === undefined) return null;
  const text = String(raw).trim();
  if (!text || ['null', 'none'].includes(text.toLowerCase())) return null;
  return text;
}

/** Whether an installation record carries a suspension. Pure. */
export function isSuspended(inst) {
  return suspendedAt(inst) !== null;
}

/** Who suspended it, where the record says. Pure. */
export function suspendedBy(inst) {
  if (!inst || typeof inst !== 'object') return null;
  const who = inst.suspended_by;
  if (who && typeof who === 'object') return who.login ? String(who.login) : null;
  if (typeof who === 'string' && who.trim()) return who.trim();
  return null;
}

/** An ISO 8601 timestamp as epoch milliseconds, or null. Pure. */
export function parsedTime(text) {
  const raw = String(text ?? '').trim();
  if (!raw) return null;
  const ms = Date.parse(raw);
  return Number.isNaN(ms) ? null : ms;
}

/** Whole days between a timestamp and now, or null. Pure. */
export function daysSince(text, nowMs) {
  const when = parsedTime(text);
  if (when === null || nowMs === null || nowMs === undefined) return null;
  return Math.floor((nowMs - when) / 86400000);
}

/** The login of the account an installation sits on. Pure. */
export function accountOf(inst) {
  if (!inst || typeof inst !== 'object') return 'an unnamed account';
  const account = inst.account;
  if (account && typeof account === 'object' && account.login) return String(account.login);
  return 'an unnamed account';
}

/** The record for one installation id, or null. Pure. Compared as text. */
export function find(installations, installationId) {
  if (installationId === null || installationId === undefined) return null;
  const wanted = String(installationId).trim();
  for (const inst of installations ?? []) {
    if (inst && typeof inst === 'object' && String(inst.id ?? '').trim() === wanted) return inst;
  }
  return null;
}

/** Counts across every installation the App can see. Pure. */
export function summarize(installations) {
  const rows = (installations ?? []).filter((i) => i && typeof i === 'object');
  const suspended = rows.filter(isSuspended);
  return {
    total: rows.length,
    suspended: suspended.length,
    active: rows.length - suspended.length,
    suspended_ids: suspended.map((i) => i.id),
  };
}

/** Turn one installation record and an optional probe into a finding. Pure. */
export function verdict(target, probeStatus = null, nowMs = null) {
  if (!target) {
    return ['not-listed',
      'this installation id is not among the ones the App can see. ' +
      'Suspension keeps the record, so an absent id means the App was ' +
      'removed and possibly reinstalled under a new id, which is a ' +
      'different repair.'];
  }
  const ident = `installation ${target.id ?? '?'} on ${accountOf(target)}`;
  const when = suspendedAt(target);
  if (when !== null) {
    const age = daysSince(when, nowMs);
    const who = suspendedBy(target);
    return ['suspended',
      `${ident} was suspended at ${when}${who ? ` by ${who}` : ''}` +
      `${age !== null ? `, ${age} day(s) ago` : ''}. Every token minted for ` +
      'it is refused and webhook delivery has stopped. Retrying cannot clear this.'];
  }
  if (probeStatus === 401 || probeStatus === 403) {
    return ['active-but-refused',
      `${ident} is listed and not suspended, yet an installation token got ` +
      `${probeStatus}. The refusal is about a permission, a route or the ` +
      'token itself rather than about suspension.'];
  }
  return ['active', `${ident} is listed and not suspended.`];
}

/** Whether a caller should ever try this installation again. Pure. */
export function retryable(state) {
  return !TERMINAL.includes(state);
}

/** The sentence a reader has to act on. Pure. */
export function repair(state, target) {
  if (state === 'suspended') {
    return `an organization owner unsuspends it from the ${accountOf(target)} ` +
      "account's Installed GitHub Apps page. Retrying cannot help: stop the " +
      'queue for this installation and alert once.';
  }
  if (state === 'not-listed') {
    return 'resolve the installation id at runtime from the org\'s own ' +
      'installation record, or from the installation.id field on an incoming ' +
      'webhook, rather than storing it.';
  }
  if (state === 'active-but-refused') {
    return 'read the accepted-permissions header on the failing response and ' +
      'diff it against the permissions this installation granted. Suspension ' +
      'is not the cause here.';
  }
  return 'nothing. This installation is usable.';
}

async function get(credential, path) {
  const url = path.startsWith('/') ? API + path : path;
  const res = await fetch(url, {
    headers: {
      Authorization: `Bearer ${credential}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': UA,
    },
  });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return { status: res.status, body };
}

async function listInstallations(jwt, pages = 10) {
  const out = [];
  for (let page = 1; page <= pages; page += 1) {
    const { status, body } = await get(jwt, `/app/installations?per_page=100&page=${page}`);
    if (status !== 200 || !Array.isArray(body)) {
      if (page === 1) {
        console.error(`GET /app/installations returned ${status}; the JWT is ` +
          'the credential this endpoint wants');
      }
      break;
    }
    out.push(...body);
    if (body.length < 100) break;
  }
  return out;
}

async function main() {
  const jwt = (process.env.GITHUB_APP_JWT || "dummy-github-app-jwt");
  if (!jwt) {
    console.error('set GITHUB_APP_JWT to the JWT your own signing code ' +
      'produced. suspended_at lives on the installation record, and ' +
      "installation records are read with the App's JWT");
    process.exitCode = 2;
    return;
  }
  const installationId = process.argv[2] ?? (process.env.GITHUB_INSTALLATION_ID || "dummy-github-installation-id") ?? null;

  const installations = await listInstallations(jwt);
  const stats = summarize(installations);
  console.log(`${stats.total} installation(s) visible to this App, ` +
    `${stats.suspended} suspended`);

  let probeStatus = null;
  const token = (process.env.GITHUB_INSTALLATION_TOKEN || "dummy-github-installation-token");
  if (token) {
    ({ status: probeStatus } = await get(token, '/installation/repositories?per_page=1'));
    console.log('installation token: GET /installation/repositories returned ' +
      `${probeStatus}`);
  }

  const now = Date.now();
  const findings = [];
  if (installationId) {
    const target = find(installations, installationId);
    const [state, detail] = verdict(target, probeStatus, now);
    findings.push({ installation_id: installationId, state, detail, retryable: retryable(state) });
    console.log(`${state}: ${detail}`);
    console.log(`repair: ${repair(state, target)}`);
  } else {
    for (const inst of installations) {
      const [state, detail] = verdict(inst, null, now);
      findings.push({ installation_id: inst.id, state, detail, retryable: retryable(state) });
      if (state !== 'active') {
        console.log(`${state}: ${detail}`);
        console.log(`repair: ${repair(state, inst)}`);
      }
    }
    if (stats.suspended === 0) {
      console.log('active: no installation of this App is suspended');
    }
  }

  console.log(JSON.stringify({
    visible: stats.total,
    suspended: stats.suspended,
    suspended_ids: stats.suspended_ids,
    probe_status: probeStatus,
    findings,
  }, null, 2));
  process.exitCode = findings.some((f) => !f.retryable) ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main(), fail on the missing JWT and set an exit code that
// fails the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
