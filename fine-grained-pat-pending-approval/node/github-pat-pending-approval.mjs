/**
 * Tell a token waiting for an owner apart from a token short a permission.
 *
 * Read only, and it never approves anything or asks for approval. The request
 * this script detects already exists: it was filed the moment the token was
 * created, so there is nothing to resubmit and resubmitting would only put a
 * duplicate into somebody queue.
 *
 * A missing permission is endpoint-shaped: whatever the token cannot do, it
 * cannot do anywhere. A pending organization approval is owner-shaped: every
 * endpoint family fails under one resource owner while personal reads succeed.
 *
 * Environment:
 *   GITHUB_TOKEN        the fine-grained token being refused
 *   GITHUB_ADMIN_TOKEN  an organization owner credential with admin:org
 *   GITHUB_ORG          the organization whose resources are refused
 */
const API = 'https://api.github.com';
const UA = 'github-pat-pending-approval/1.0';

export const SSO_HEADER = 'x-github-sso';
export const ACCEPTED_PERMISSIONS_HEADER = 'x-accepted-github-permissions';

export const TOKEN_PREFIXES = [
  ['github_pat_', 'fine-grained PAT'],
  ['ghp_', 'classic PAT'],
  ['gho_', 'OAuth user token'],
  ['ghu_', 'App user-to-server token'],
  ['ghs_', 'App installation token'],
  ['ghr_', 'App refresh token'],
];

export const PERSONAL_PROBES = [
  ['user', '/user'],
  ['repositories', '/user/repos?per_page=1'],
  ['issues', '/issues?per_page=1'],
];
export const ORG_PROBES = [
  ['repositories', (org) => `/orgs/${org}/repos?per_page=1`],
  ['issues', (org) => `/orgs/${org}/issues?per_page=1`],
  ['members', (org) => `/orgs/${org}/members?per_page=1`],
];

export const REFUSED = [403, 404];

export const OAUTH_RESTRICTION_PHRASES = [
  'oauth app access restrictions',
  'third-party application',
];

/** Requests this run will spend against the core quota. Pure. */
export function readCost(withAdmin = false) {
  return PERSONAL_PROBES.length + ORG_PROBES.length + (withAdmin ? 1 : 0);
}

/** Name the credential from its prefix. Pure. */
export function tokenKind(token) {
  const value = String(token ?? '').trim();
  for (const [prefix, name] of TOKEN_PREFIXES) {
    if (value.startsWith(prefix)) return name;
  }
  return 'unknown';
}

/** Is this failure shaped like an owner or like an endpoint. Pure. */
export function probeShape(personal, org) {
  if (org.length < 2) {
    return ['insufficient-evidence', 'fewer than two organization endpoint '
      + 'families were read, and one family cannot show whether refusals '
      + 'cluster by owner.'];
  }
  const refused = (s) => REFUSED.includes(s);
  const orgRefused = org.filter(([, s]) => refused(s)).map(([f]) => f);
  const orgOk = org.filter(([, s]) => s === 200).map(([f]) => f);
  const personalOk = personal.filter(([, s]) => s === 200).map(([f]) => f);
  const personalRefused = personal.filter(([, s]) => refused(s)).map(([f]) => f);

  if (personalOk.length === 0) {
    return ['credential-shaped', 'nothing succeeded in the personal namespace '
      + 'either, so the credential itself is the thing to look at first.'];
  }
  if (orgRefused.length === org.length && personalRefused.length === 0) {
    return ['owner-shaped', 'every organization family is refused and no '
      + 'personal family is, so the gate is the resource owner and not any endpoint.'];
  }
  if (orgOk.length > 0 && orgRefused.length > 0) {
    const shared = orgRefused.filter((f) => personalRefused.includes(f)).sort();
    if (shared.length > 0) {
      return ['endpoint-shaped', `the same family is refused in both namespaces `
        + `(${shared.join(', ')}), which is a permission the token does not hold `
        + 'rather than an owner refusing it.'];
    }
    return ['endpoint-shaped', 'some organization families answer and others do '
      + 'not, so the owner is admitting this token and individual permissions '
      + 'are what is short.'];
  }
  if (orgRefused.length === 0) {
    return ['nothing-refused', 'every family answered in both namespaces, so '
      + 'nothing is waiting on anybody today.'];
  }
  return ['unclassified-shape', 'the pattern does not match owner-shaped or '
    + 'endpoint-shaped; report the statuses rather than naming a cause.'];
}

/** The sentence that saves an hour. Pure. */
export function headerIsNotTheDiscriminator() {
  return 'x-accepted-github-permissions describes what the endpoint accepts and '
    + 'never what the token holds, so it cannot settle this either way.';
}

/** Did the refusal blame an OAuth App restriction. Pure. */
export function oauthWording(message) {
  const text = String(message ?? '').toLowerCase();
  return OAUTH_RESTRICTION_PHRASES.some((p) => text.includes(p));
}

/** The verdict. Pure. [state, detail]. */
export function classify(shape, kind, ssoSeen, oauthSeen) {
  if (kind !== 'fine-grained PAT') {
    return ['not-a-fine-grained-token', 'organization approval policy applies to '
      + `fine-grained personal access tokens. A ${kind} is governed by something `
      + 'else, with a different repair.'];
  }
  if (ssoSeen) {
    return ['saml-enforcement', 'a refusal carried x-github-sso, so SAML '
      + 'enforcement is in play and that is a different note.'];
  }
  if (oauthSeen) {
    return ['oauth-app-restriction', 'the refusal blamed OAuth App access '
      + 'restrictions, which govern applications rather than personal tokens.'];
  }
  if (shape === 'owner-shaped') {
    return ['pending-org-approval', 'this token is waiting for an organization '
      + 'owner to approve it. Its permissions are held on paper and none in '
      + 'practice, which is why editing them changes nothing.'];
  }
  if (shape === 'endpoint-shaped') {
    return ['permission-shaped', 'the refusals follow an endpoint family rather '
      + 'than an owner, so this is a permission the token does not hold.'];
  }
  if (shape === 'credential-shaped') {
    return ['credential-problem', 'personal reads are failing too, so start with '
      + 'the credential.'];
  }
  if (shape === 'nothing-refused') {
    return ['not-blocked', 'nothing was refused during this run.'];
  }
  return ['undetermined', 'not enough evidence to name a cause.'];
}

/** Whole days a request has been waiting. Pure. */
export function daysPending(createdAt, nowMs) {
  if (!createdAt) return null;
  const when = Date.parse(String(createdAt));
  if (Number.isNaN(when)) return null;
  return Math.floor((nowMs - when) / 86400000);
}

/** The pending request filed by this account, if any. Pure. */
export function findRequest(requestsList, login) {
  for (const item of requestsList || []) {
    if (!item || typeof item !== 'object') continue;
    const owner = item.owner || {};
    if (String(owner.login ?? '').toLowerCase() === String(login ?? '').toLowerCase()) {
      return item;
    }
  }
  return null;
}

/** The sentence a reader has to act on. Pure. */
export function repair(state, org) {
  if (state === 'pending-org-approval') {
    return `an owner of ${org} approves the waiting request under the `
      + 'organization personal access tokens settings. This script does not '
      + 'approve it and does not ask for it. Do not create a replacement token: '
      + 'the request already exists and a new one only queues behind it.';
  }
  if (state === 'permission-shaped') {
    return 'read x-accepted-github-permissions off the refusal, tick that '
      + 'permission on the token, and expect the organization to re-approve it.';
  }
  if (state === 'saml-enforcement') {
    return 'follow the SSO authorization URL on the refusal instead.';
  }
  if (state === 'oauth-app-restriction') {
    return 'have an owner approve the application; this is a policy about an app.';
  }
  if (state === 'not-a-fine-grained-token') {
    return 'find the gate that applies to this credential type first.';
  }
  if (state === 'credential-problem') {
    return 'fix the credential; no organization queue is involved yet.';
  }
  if (state === 'not-blocked') {
    return `nothing. This token is reaching ${org} right now.`;
  }
  return 'read more endpoint families and run this again.';
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
    console.error('set GITHUB_TOKEN (the fine-grained token) and GITHUB_ORG');
    process.exitCode = 2;
    return;
  }
  const admin = (process.env.GITHUB_ADMIN_TOKEN || "dummy-github-admin-token");
  console.log(`read cost: ${readCost(Boolean(admin))} request(s) against the core hourly quota`);

  const kind = tokenKind(token);
  const personal = [];
  let account = null;
  for (const [family, path] of PERSONAL_PROBES) {
    const response = await fetch(API + path, { headers: headers(token) });
    personal.push([family, response.status]);
    if (family === 'user' && response.status === 200) {
      account = (await response.json()).login;
    }
  }
  console.log(`credential: ${kind}, account=${account ?? 'unreadable'}`);
  console.log(`personal  ${personal.map(([f, s]) => `${f}=${s}`).join('  ')}`);

  const orgResults = [];
  let ssoSeen = false;
  let oauthSeen = false;
  let acceptedSeen = null;
  for (const [family, template] of ORG_PROBES) {
    const response = await fetch(API + template(org), { headers: headers(token) });
    orgResults.push([family, response.status]);
    if (REFUSED.includes(response.status)) {
      if (response.headers.get(SSO_HEADER)) ssoSeen = true;
      acceptedSeen = response.headers.get(ACCEPTED_PERMISSIONS_HEADER) || acceptedSeen;
      try {
        const body = await response.json();
        if (body && typeof body === 'object' && oauthWording(body.message)) oauthSeen = true;
      } catch { /* an empty body is not evidence either way */ }
    }
  }
  console.log(`org       ${orgResults.map(([f, s]) => `${f}=${s}`).join('  ')}`);

  const [shape, shapeDetail] = probeShape(personal, orgResults);
  console.log(`shape: ${shape} - ${shapeDetail}`);
  console.log(`${SSO_HEADER}: ${ssoSeen ? 'present on a refusal' : 'absent on every refusal'}`);
  console.log(`note: ${headerIsNotTheDiscriminator()}`);

  const [state, detail] = classify(shape, kind, ssoSeen, oauthSeen);
  console.log(`${state}: ${detail}`);

  let pending = null;
  let waitingDays = null;
  if (admin) {
    const listing = await fetch(
      `${API}/orgs/${org}/personal-access-token-requests?per_page=100`,
      { headers: headers(admin) },
    );
    if (listing.status === 200) {
      const body = await listing.json();
      pending = findRequest(Array.isArray(body) ? body : [], account);
      if (pending) {
        waitingDays = daysPending(pending.created_at, Date.now());
        console.log(`pending request: filed ${waitingDays} day(s) ago by ${account}`);
      } else {
        console.log(`pending request: none filed by ${account} is waiting`);
      }
    } else {
      console.warn(`personal-access-token-requests returned HTTP ${listing.status}; `
        + 'that endpoint needs admin:org');
    }
  }

  console.log(`repair: ${repair(state, org)}`);
  console.log(JSON.stringify({
    organization: org,
    account,
    credential_kind: kind,
    personal: Object.fromEntries(personal),
    org: Object.fromEntries(orgResults),
    shape,
    shape_detail: shapeDetail,
    sso_header_seen: ssoSeen,
    oauth_wording_seen: oauthSeen,
    accepted_permissions_header: acceptedSeen,
    accepted_permissions_note: headerIsNotTheDiscriminator(),
    pending_request_found: Boolean(pending),
    pending_request_days: waitingDays,
    state,
    detail,
    repair: repair(state, org),
  }, null, 2));
  process.exitCode = state === 'pending-org-approval' ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
