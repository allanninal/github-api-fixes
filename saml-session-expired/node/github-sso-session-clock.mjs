/**
 * Read the expiry on a SAML credential authorization before it lapses.
 *
 * Read only, and it authorizes nothing. The repair for a lapsed SAML session is
 * a person re-authenticating in a browser; this reports the date that will
 * become necessary and never performs it.
 *
 * Two credentials on purpose: the one in trouble, and an organization owner one
 * that can read GET /orgs/{org}/credential-authorizations, where the dated
 * record lives. The match on token_last_eight happens in memory and those
 * characters are never logged or serialised.
 *
 * Environment:
 *   GITHUB_TOKEN        the credential being diagnosed
 *   GITHUB_ADMIN_TOKEN  an organization owner credential with admin:org
 *   GITHUB_ORG          the organization enforcing SAML
 */
const API = 'https://api.github.com';
const UA = 'github-sso-session-clock/1.0';

export const SSO_HEADER = 'x-github-sso';
export const EXPIRING_SOON_DAYS = 7;

export const TOKEN_PREFIXES = [
  ['github_pat_', 'fine-grained PAT'],
  ['ghp_', 'classic PAT'],
  ['gho_', 'OAuth user token'],
  ['ghu_', 'App user-to-server token'],
  ['ghs_', 'App installation token'],
  ['ghr_', 'App refresh token'],
];

/** Credentials that hang off a person identity-provider session. */
export const LAPSES_WITH_A_PERSON = {
  'classic PAT': true,
  'OAuth user token': true,
  'App user-to-server token': true,
  'fine-grained PAT': true,
  'App installation token': false,
  'App refresh token': false,
  unknown: true,
};

/** Requests this run will spend against the core quota. Pure. */
export function readCost(withAdmin = true, pages = 1) {
  return 2 + (withAdmin ? pages : 0);
}

/** Name the credential from its prefix. Pure. */
export function tokenKind(token) {
  const value = String(token ?? '').trim();
  for (const [prefix, name] of TOKEN_PREFIXES) {
    if (value.startsWith(prefix)) return name;
  }
  return 'unknown';
}

/** The eight characters a record is matched on. Pure, and never printed. */
export function lastEight(token) {
  const value = String(token ?? '').trim();
  return value.length >= 8 ? value.slice(-8) : '';
}

/** Find the record for this credential. Pure. */
export function matchAuthorization(records, tail) {
  if (!tail) return null;
  for (const record of records || []) {
    if (!record || typeof record !== 'object') continue;
    if (String(record.token_last_eight ?? '') === tail) return record;
  }
  return null;
}

/** ISO 8601 into epoch milliseconds, or null. Pure. */
export function parseTs(value) {
  if (!value) return null;
  const ms = Date.parse(String(value));
  return Number.isNaN(ms) ? null : ms;
}

/** Whole days until the grant lapses, negative if it has. Pure. */
export function daysLeft(expiresAt, nowMs) {
  const when = parseTs(expiresAt);
  if (when === null) return null;
  return Math.floor((when - nowMs) / 86400000);
}

/** Classify one credential SAML standing. Pure. [state, detail]. */
export function authorizationState(record, nowMs, refused) {
  if (!record) {
    if (refused) {
      return ['never-authorized', 'no authorization record exists for this '
        + 'credential, so it has never been authorized for this organization. '
        + 'That is a first authorization rather than a lapse.'];
    }
    return ['no-record-no-refusal', 'no authorization record and nothing being '
      + 'refused, which is what an organization without SAML looks like.'];
  }
  const remaining = daysLeft(record.authorized_credential_expires_at, nowMs);
  if (remaining === null) {
    return ['expiry-not-published', 'the record exists but carries no expiry, so '
      + 'this grant is not on a clock the API will show you.'];
  }
  if (remaining < 0) {
    return ['authorization-lapsed', `this authorization expired ${Math.abs(remaining)} `
      + 'day(s) ago. The credential is unchanged and valid; the SAML session '
      + 'behind it ran out.'];
  }
  if (remaining <= EXPIRING_SOON_DAYS) {
    return ['authorization-expiring', `this authorization lapses in ${remaining} `
      + 'day(s). The credential is fine; the SAML session behind it runs out.'];
  }
  return ['authorization-active', `this authorization is good for another ${remaining} day(s).`];
}

/** Did this credential demonstrably work here. Pure. */
export function lapseEvidence(record) {
  if (!record) return [false, 'no record, so there is no evidence of past use.'];
  const used = parseTs(record.credential_accessed_at);
  if (used === null) {
    return [false, 'the record carries no last-used time, so past success is not '
      + 'provable from it.'];
  }
  return [true, `the record was last used at ${new Date(used).toISOString()}, `
    + 'which proves this credential did work against this organization.'];
}

/** What recurrence a reader should expect. Pure. */
export function cadenceNote(state) {
  const clocked = ['authorization-lapsed', 'authorization-expiring',
    'authorization-active'];
  if (clocked.includes(state)) {
    return 'the organization re-authentication interval is not published by the '
      + 'API. What is readable is this grant expiry, and it will recur.';
  }
  return 'nothing to forecast from this reading.';
}

/** Does this credential type depend on a person staying logged in. Pure. */
export function unattendedVerdict(kind) {
  if (LAPSES_WITH_A_PERSON[kind] ?? true) {
    return [true, `a ${kind} hangs off a person identity-provider session, so an `
      + 'unattended job holding one fails whenever that person stops logging in.'];
  }
  return [false, `a ${kind} does not depend on anyone identity-provider session, `
    + 'which is why it is the answer for unattended work.'];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state, org, kind) {
  const [depends] = unattendedVerdict(kind);
  const moveOff = depends ? ' For anything unattended, move to an App '
    + 'installation token, which never lapses with a person session.' : '';
  const renew = `a person re-authenticates at https://github.com/orgs/${org}/sso. `
    + 'This script does not and will not do it.';
  if (state === 'authorization-lapsed') return renew + moveOff;
  if (state === 'authorization-expiring') return `${renew} Do it before that date.${moveOff}`;
  if (state === 'never-authorized') {
    return 'authorize the credential for the first time, which is the sibling '
      + 'problem: the refusal is the same and the repair does not recur.';
  }
  if (state === 'authorization-active') {
    return 'nothing today. Note the date and decide whether an unattended job '
      + 'should depend on a human session at all.';
  }
  if (state === 'expiry-not-published') {
    return 'read the refusal x-github-sso header instead; this record will not '
      + 'tell you when the grant ends.';
  }
  return 'nothing on SAML here.';
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
    console.error('set GITHUB_TOKEN (the credential being diagnosed) and GITHUB_ORG');
    process.exitCode = 2;
    return;
  }
  const admin = (process.env.GITHUB_ADMIN_TOKEN || "dummy-github-admin-token");
  console.log(`read cost: ${readCost(Boolean(admin))} request(s) against the core hourly quota`);

  const kind = tokenKind(token);
  const me = await fetch(`${API}/user`, { headers: headers(token) });
  const account = me.status === 200 ? (await me.json()).login : null;
  console.log(`credential: ${kind}, account=${account ?? 'unreadable'}`);

  const listing = await fetch(`${API}/orgs/${org}/repos?per_page=1`,
    { headers: headers(token) });
  const ssoForm = (listing.headers.get(SSO_HEADER) || '').split(';')[0].trim().toLowerCase();
  console.log(`GET /orgs/${org}/repos -> ${listing.status}, ${SSO_HEADER}: ${ssoForm || 'absent'}`);
  const refused = listing.status === 403 || listing.status === 404;

  let records = [];
  if (admin) {
    const page = await fetch(
      `${API}/orgs/${org}/credential-authorizations?per_page=100`,
      { headers: headers(admin) },
    );
    if (page.status === 200) {
      const body = await page.json();
      records = Array.isArray(body) ? body : [];
    } else {
      console.warn(`credential-authorizations returned HTTP ${page.status}; that `
        + 'endpoint needs admin:org.');
    }
  } else {
    console.warn('no GITHUB_ADMIN_TOKEN, so the dated record cannot be read and a '
      + 'lapse looks exactly like a first authorization.');
  }

  // Compared in memory. These characters are never logged or serialised.
  const record = matchAuthorization(records, lastEight(token));
  console.log(`credential-authorizations: ${records.length} record(s) read, `
    + `${record ? 1 : 0} matched`);

  const now = Date.now();
  const [state, detail] = authorizationState(record, now, refused);
  const [proven, proof] = lapseEvidence(record);
  console.log(`${state}: ${detail}`);
  console.log(`past use: ${proof}`);
  console.log(`cadence: ${cadenceNote(state)}`);
  const [depends, dependsDetail] = unattendedVerdict(kind);
  console.log(`unattended: ${dependsDetail}`);
  console.log(`repair: ${repair(state, org, kind)}`);

  console.log(JSON.stringify({
    organization: org,
    account,
    credential_kind: kind,
    listing_status: listing.status,
    sso_form: ssoForm || null,
    records_read: records.length,
    record_matched: Boolean(record),
    credential_accessed_at: record?.credential_accessed_at ?? null,
    authorized_credential_expires_at: record?.authorized_credential_expires_at ?? null,
    days_left: daysLeft(record?.authorized_credential_expires_at, now),
    state,
    detail,
    past_use_proven: proven,
    depends_on_a_person: depends,
    repair: repair(state, org, kind),
  }, null, 2));
  process.exitCode = ['authorization-lapsed', 'authorization-expiring'].includes(state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
