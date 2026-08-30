/**
 * Compare a token's granted lifetime against the interval you rotate on.
 *
 * Read only. One free call plus two optional cheap ones. Nothing is minted,
 * rotated or revoked: the repair is a schedule change and a policy only an
 * organization owner can alter, and both are printed rather than performed.
 *
 * An organization can cap how long a fine-grained token may live. The failure
 * that produces is not an expiry: the policy blocks a non-compliant token at
 * that organization while it keeps working everywhere else.
 *
 * Environment:
 *   GITHUB_TOKEN         the credential whose lifetime is in question
 *   GITHUB_ISSUED        optional YYYY-MM-DD the token was minted
 *   GITHUB_ROTATION_DAYS optional rotation interval from the runbook
 *   GITHUB_ORG_MAX_DAYS  optional declared cap; not readable through the API
 *   GITHUB_ORG           optional organization to probe
 *   GITHUB_ORG_GRANTS    set to 1 to list the org's fine-grained grants
 */
const API = 'https://api.github.com';
const UA = 'github-token-lifetime/1.0';

export const HEADER = 'github-authentication-token-expiration';
export const DAY = 86400;

/** The documented default ceiling for a fine-grained token. A policy can be shorter. */
export const DEFAULT_FINE_GRAINED_MAX_DAYS = 366;

export const TOKEN_PREFIXES = [
  ['github_pat_', 'fine-grained PAT'],
  ['ghp_', 'classic PAT'],
  ['gho_', 'OAuth user token'],
  ['ghu_', 'App user-to-server token'],
  ['ghs_', 'App installation token'],
  ['ghr_', 'App refresh token'],
];

/** [requests, quota units] this run will spend. Pure. */
export function readCost(withOrg, withGrants) {
  const made = 1 + (withOrg ? 1 : 0) + (withGrants ? 1 : 0);
  return [made, made - 1];
}

/** Name the credential from its prefix. Pure. */
export function tokenKind(token) {
  const value = String(token ?? '').trim();
  for (const [prefix, name] of TOKEN_PREFIXES) {
    if (value.startsWith(prefix)) return name;
  }
  return 'unknown';
}

/** Does a maximum-lifetime policy govern this class. Pure. [state, detail]. */
export function policyApplies(kind) {
  if (kind === 'fine-grained PAT') {
    return ['policy-applies', 'the maximum-lifetime policy applies to this '
      + `class. The documented default ceiling is ${DEFAULT_FINE_GRAINED_MAX_DAYS} `
      + 'days and an organization or enterprise can set something much shorter.'];
  }
  if (kind === 'classic PAT') {
    return ['different-class', 'classic tokens have no expiry requirement, so a '
      + 'maximum-lifetime policy does not cover them. An organization restricts '
      + 'them by blocking classic access altogether, which is a different '
      + 'refusal, and a classic token that dies after a long silence is the '
      + 'auto-revocation note.'];
  }
  if (kind === 'App installation token' || kind === 'App refresh token') {
    return ['minted-hourly', 'installation tokens live about an hour and are '
      + 'minted on demand, so there is no lifetime for a policy to cap and no '
      + 'rotation for a runbook to schedule.'];
  }
  if (kind === 'OAuth user token' || kind === 'App user-to-server token') {
    return ['different-model', "this credential's life is governed by its "
      + 'authorization and refresh flow rather than by a token lifetime policy.'];
  }
  return ['class-unknown', 'the credential class could not be named from its '
    + 'prefix, so whether the policy applies is unknown.'];
}

/** Epoch seconds from a timestamp, or null. Pure. No regular expression. */
export function parseStamp(value) {
  if (typeof value !== 'string') return null;
  let text = value.trim();
  if (!text) return null;
  const upper = text.toUpperCase();
  if (upper.endsWith(' UTC') || upper.endsWith(' GMT')) text = text.slice(0, -4).trim();
  else if (upper.endsWith('Z')) text = text.slice(0, -1).trim();
  text = text.split('T').join(' ');
  const cut = text.indexOf(' ');
  const datePart = cut === -1 ? text : text.slice(0, cut);
  const timePart = cut === -1 ? '' : text.slice(cut + 1).trim();
  const bits = datePart.split('-');
  const digits = (s) => s.length > 0 && [...s].every((c) => c >= '0' && c <= '9');
  if (bits.length !== 3 || !bits.every(digits)) return null;
  let hour = 0;
  let minute = 0;
  let second = 0;
  if (timePart) {
    const clock = timePart.split(':').map((c) => c.split('.')[0]);
    if (!clock.every(digits)) return null;
    [hour = 0, minute = 0, second = 0] = clock.map(Number);
  }
  const ms = Date.UTC(Number(bits[0]), Number(bits[1]) - 1, Number(bits[2]),
    hour, minute, second);
  return Number.isNaN(ms) ? null : ms / 1000;
}

/** Case-insensitive header read against a plain object. Pure. */
export function headerValue(headers, name = HEADER) {
  if (!headers || typeof headers !== 'object') return null;
  const wanted = String(name).toLowerCase();
  for (const key of Object.keys(headers)) {
    if (key.toLowerCase() === wanted) return headers[key];
  }
  return null;
}

/** Days between two epochs, or null. Pure. */
export function daysBetween(earlier, later) {
  if (earlier === null || earlier === undefined) return null;
  if (later === null || later === undefined) return null;
  return (later - earlier) / DAY;
}

/** The life this token was actually given, or null. Pure. */
export function grantedLifetimeDays(issuedEpoch, expiresEpoch) {
  const span = daysBetween(issuedEpoch, expiresEpoch);
  if (span === null || span <= 0) return null;
  return span;
}

/** Is the granted lifetime over the declared cap. Pure. [state, detail]. */
export function capVerdict(grantedDays, orgMaxDays) {
  if (orgMaxDays === null || orgMaxDays === undefined) {
    return ['cap-not-declared', 'no maximum was declared. There is no '
      + "documented endpoint that returns an organization's maximum-lifetime "
      + 'setting, so this number has to come from a person.'];
  }
  if (grantedDays === null || grantedDays === undefined) {
    return ['lifetime-unknown', 'the granted lifetime is unknown without an '
      + 'issue date, so it cannot be compared against the cap.'];
  }
  if (grantedDays > orgMaxDays) {
    return ['over-org-cap', `the granted lifetime is ${Math.round(grantedDays)} `
      + `day(s), longer than the declared cap of ${orgMaxDays}. A token over the `
      + 'cap is blocked at that organization while it keeps working everywhere '
      + 'else: the policy refuses tokens, it does not shorten them.'];
  }
  return ['within-org-cap', `the granted lifetime of ${Math.round(grantedDays)} `
    + `day(s) is inside the declared cap of ${orgMaxDays}.`];
}

/** Compare two periods, not a date and today. Pure. [state, detail]. */
export function rotationFit(grantedDays, remainingDays, rotationDays) {
  if (rotationDays === null || rotationDays === undefined) {
    return ['rotation-not-declared', 'no rotation interval was declared, so '
      + 'there is nothing to compare a lifetime against.'];
  }
  if (remainingDays !== null && remainingDays !== undefined && remainingDays < 0) {
    return ['already-expired', 'the expiry is in the past. That is the ordinary '
      + 'expiry note, not a policy problem.'];
  }
  if (grantedDays !== null && grantedDays !== undefined && rotationDays > grantedDays) {
    return ['rotation-outlives-token', `you rotate every ${rotationDays} day(s) `
      + `and this token was granted ${Math.round(grantedDays)}. That breaks once `
      + 'per cycle, forever, and rotating earlier this once will not change it.'];
  }
  if (remainingDays !== null && remainingDays !== undefined && rotationDays > remainingDays) {
    return ['this-cycle-expires-first', `this token dies in `
      + `${Math.round(remainingDays)} day(s) and the next scheduled rotation is `
      + `${rotationDays} away. A one-off: rotate early and the schedule is still sound.`];
  }
  if (grantedDays === null || grantedDays === undefined) {
    return ['lifetime-unknown', 'days remaining are known and the granted '
      + 'lifetime is not, so whether the schedule works in general cannot be '
      + 'decided from this reading.'];
  }
  return ['fits', 'the rotation interval is inside the granted lifetime, so the '
    + 'schedule works on its own terms.'];
}

/** What a missing expiry header means for this class. Pure. [state, detail]. */
export function expiryAbsentMeaning(kind) {
  if (kind === 'classic PAT') {
    return ['no-expiry-on-this-class', 'a classic token with no expiry emits no '
      + 'header. That is not reassurance: a credential that never expires is a '
      + 'larger exposure than one that does, and it has its own note.'];
  }
  if (kind === 'App installation token' || kind === 'App refresh token') {
    return ['short-lived-by-design', 'this class is minted for about an hour, so '
      + 'an absent header is the expected state and nothing here needs an alarm.'];
  }
  return ['expiry-not-reported', 'no expiry header came back for a class that '
    + 'usually carries one. Either the response was not authenticated or this '
    + 'credential has no expiry at all; check which before concluding anything.'];
}

/** The shape of a policy block, without claiming it. Pure. [state, detail]. */
export function orgProbeVerdict(selfStatus, orgStatus) {
  const mine = Number(selfStatus) || 0;
  const theirs = (orgStatus === null || orgStatus === undefined) ? null : Number(orgStatus);
  if (![200, 204].includes(mine)) {
    return ['credential-dead', 'the credential did not authenticate at all, so '
      + "nothing here is about one organization's policy."];
  }
  if (theirs === null) {
    return ['org-not-probed', 'no organization was probed, so the reading is '
      + 'about the credential in general rather than about one namespace.'];
  }
  if ([200, 204].includes(theirs)) {
    return ['org-reachable', 'the organization answered, so nothing is blocking '
      + 'this credential there right now.'];
  }
  if ([401, 403, 404].includes(theirs)) {
    return ['refused-by-one-org', 'the credential authenticates globally and is '
      + 'refused at this organization. Three things produce that shape: a token '
      + 'over a lifetime policy, a fine-grained token still waiting for owner '
      + 'approval, and a SAML authorization that has lapsed. Each has its own '
      + 'note; this reading narrows the search rather than ending it.'];
  }
  return ['org-probe-inconclusive', `HTTP ${orgStatus} from the organization is `
    + 'not a refusal or a success, so it says nothing about policy.'];
}

/** Which fine-grained tokens reaching the org die when. Pure. */
export function grantsOverCap(grants, orgMaxDays, nowEpoch) {
  const out = [];
  for (const grant of grants || []) {
    if (!grant || typeof grant !== 'object') continue;
    const owner = (grant.owner && grant.owner.login) || null;
    const expires = grant.token_expires_at ? parseStamp(grant.token_expires_at) : null;
    const remaining = expires ? daysBetween(nowEpoch, expires) : null;
    out.push({
      owner,
      token_expires_at: grant.token_expires_at ?? null,
      expired: Boolean(grant.token_expired),
      days_remaining: remaining === null ? null : Math.round(remaining * 10) / 10,
      no_expiry: (grant.token_expires_at ?? null) === null,
      over_declared_cap: (orgMaxDays !== null && orgMaxDays !== undefined
        && (grant.token_expires_at ?? null) === null),
    });
  }
  out.sort((a, b) => {
    if (a.days_remaining === null) return 1;
    if (b.days_remaining === null) return -1;
    return a.days_remaining - b.days_remaining;
  });
  return out;
}

/** The finding, in one state. Pure. [state, detail]. */
export function verdict(capState, fitState, appliesState) {
  if (['different-class', 'minted-hourly', 'different-model'].includes(appliesState)) {
    return [appliesState, 'a maximum-lifetime policy does not govern this '
      + 'credential class, so this note is not about your problem.'];
  }
  if (capState === 'over-org-cap') {
    return ['blocked-by-lifetime-policy', 'this token is longer-lived than the '
      + 'declared cap, which is the state that gets refused at that '
      + 'organization while every global check on the credential passes.'];
  }
  if (fitState === 'rotation-outlives-token') {
    return ['schedule-cannot-work', 'the rotation interval is longer than any '
      + 'lifetime available here. This is a process finding, not an incident, '
      + 'and it will produce an outage every cycle until the schedule changes.'];
  }
  if (fitState === 'this-cycle-expires-first') {
    return ['rotate-early-this-once', 'this particular token dies before the '
      + 'next scheduled rotation. Bring the rotation forward; the schedule '
      + 'itself is sound.'];
  }
  if (fitState === 'already-expired') {
    return ['expired', 'the expiry has passed, which is the plain expiry case '
      + 'and has its own note.'];
  }
  if (capState.includes('unknown') || fitState.includes('unknown')) {
    return ['lifetime-unknown', 'not enough was supplied to compare periods. '
      + 'The issue date and the rotation interval are both facts only you hold.'];
  }
  return ['within-policy', 'the granted lifetime is inside the declared cap and '
    + 'the rotation interval is inside the lifetime.'];
}

/** The sentence a reader has to act on. Pure. Nothing here rotates. */
export function repair(state, rotationDays, orgMaxDays) {
  if (['blocked-by-lifetime-policy', 'schedule-cannot-work'].includes(state)) {
    const cap = (orgMaxDays === null || orgMaxDays === undefined)
      ? 'the enforced maximum' : orgMaxDays;
    return `shorten the rotation interval to fit inside ${cap} day(s) and alert `
      + 'on the expiry header rather than on a calendar. Where that cadence is '
      + 'impractical, move this job to a GitHub App whose installation tokens '
      + 'are minted hourly and need no rotation at all. Nothing here rotates '
      + 'anything.';
  }
  if (state === 'rotate-early-this-once') {
    return 'bring this rotation forward: the token dies before the next '
      + `scheduled one. The interval of ${rotationDays} day(s) is otherwise fine.`;
  }
  if (state === 'expired') {
    return 'mint a replacement. This is the plain expiry case and the policy '
      + 'comparison is not what failed.';
  }
  if (state === 'lifetime-unknown') {
    return 'supply the issue date recorded when this token was minted and the '
      + 'rotation interval from the runbook, then re-run. Neither is on the wire.';
  }
  if (['different-class', 'minted-hourly', 'different-model'].includes(state)) {
    return 'no action from this note; the credential class is not the one a '
      + 'lifetime policy governs.';
  }
  return 'nothing to repair from this reading.';
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
  if (!token) {
    console.error('set GITHUB_TOKEN (the credential whose lifetime is in question)');
    process.exitCode = 2;
    return;
  }
  const org = (process.env.GITHUB_OR || "dummy-github-or")G || '';
  const withGrants = (process.env.GITHUB_ORG_GRANTS || "dummy-github-org-grants") === '1';
  const rotationDays = (process.env.GITHUB_ROTATION_DAYS || "dummy-github-rotation-days")
    ? Number((process.env.GITHUB_ROTATION_DAYS || "dummy-github-rotation-days")) : null;
  const orgMaxDays = (process.env.GITHUB_ORG_MAX_DAYS || "dummy-github-org-max-days")
    ? Number((process.env.GITHUB_ORG_MAX_DAYS || "dummy-github-org-max-days")) : null;
  const [made, spent] = readCost(Boolean(org), withGrants);
  console.log(`read cost: ${made} REST request(s), ${spent} of which count `
    + 'against the core quota');

  const kind = tokenKind(token);
  const [appliesState, appliesDetail] = policyApplies(kind);
  console.log(`credential: ${kind}. ${appliesDetail}`);

  const probe = await fetch(`${API}/rate_limit`, { headers: headers(token) });
  const headerBag = {};
  probe.headers.forEach((value, key) => { headerBag[key] = value; });
  const rawExpiry = headerValue(headerBag);
  const now = Date.now() / 1000;
  const expiresEpoch = parseStamp(rawExpiry);
  const remaining = daysBetween(now, expiresEpoch);
  if (rawExpiry) {
    console.log(`expiry header: ${rawExpiry} (`
      + `${remaining === null ? 'unknown' : Math.round(remaining)} day(s) remaining)`);
  } else {
    const [absentState, absentDetail] = expiryAbsentMeaning(kind);
    console.log(`${absentState}: ${absentDetail}`);
  }

  const issuedEpoch = (process.env.GITHUB_ISSUED || "dummy-github-issued")
    ? parseStamp((process.env.GITHUB_ISSUED || "dummy-github-issued")) : null;
  const granted = grantedLifetimeDays(issuedEpoch, expiresEpoch);
  if (granted !== null) {
    console.log(`granted lifetime: ${Math.round(granted)} day(s), from the issue `
      + 'date you supplied');
  }

  const [capState, capDetail] = capVerdict(granted, orgMaxDays);
  console.log(`${capState}: ${capDetail}`);
  const [fitState, fitDetail] = rotationFit(granted, remaining, rotationDays);
  console.log(`${fitState}: ${fitDetail}`);

  let orgStatus = null;
  if (org) {
    const orgProbe = await fetch(`${API}/orgs/${org}/repos?per_page=1`,
      { headers: headers(token) });
    orgStatus = orgProbe.status;
  }
  const [shapeState, shapeDetail] = orgProbeVerdict(probe.status, orgStatus);
  console.log(`org probe: ${shapeState} - ${shapeDetail}`);

  let grants = [];
  if (withGrants && org) {
    const listing = await fetch(
      `${API}/orgs/${org}/personal-access-tokens?per_page=100`,
      { headers: headers(token) },
    );
    if (listing.status === 200) {
      grants = grantsOverCap(await listing.json(), orgMaxDays, now);
      console.log(`org grants: ${grants.length} fine-grained token(s) reach ${org}`);
    } else {
      console.log(`org grants unreadable (HTTP ${listing.status}). That endpoint `
        + "is usable only by a GitHub App with the organization's personal "
        + 'access token permission.');
    }
  }

  const [state, detail] = verdict(capState, fitState, appliesState);
  console.log(`${state}: ${detail}`);
  const fix = repair(state, rotationDays, orgMaxDays);
  console.log(`repair: ${fix}`);

  console.log(JSON.stringify({
    token_kind: kind,
    policy_applies: appliesState,
    expiry_header: rawExpiry,
    days_remaining: remaining === null ? null : Math.round(remaining * 10) / 10,
    granted_lifetime_days: granted === null ? null : Math.round(granted * 10) / 10,
    declared_org_max_days: orgMaxDays,
    declared_rotation_days: rotationDays,
    cap_state: capState,
    rotation_state: fitState,
    org_probe_state: shapeState,
    org_grants: grants.slice(0, 20),
    state,
    detail,
    repair: fix,
  }, null, 2));
  process.exitCode = ['blocked-by-lifetime-policy', 'schedule-cannot-work',
    'rotate-early-this-once', 'expired'].includes(state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
