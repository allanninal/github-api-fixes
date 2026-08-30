/**
 * Show that an organization is refusing an application rather than a token.
 *
 * Read only, and it approves nothing: approving an OAuth App for an
 * organization is an owner decision made in the organization settings, there is
 * no API that performs it, and this script neither asks for it nor pretends to.
 *
 * The verdict is a behavioural shape plus two absences: one token reading two
 * namespaces, a refusal with no x-github-sso header on it, and no endpoint
 * anywhere that publishes the policy.
 *
 * Environment:
 *   GITHUB_TOKEN    a token issued by the OAuth App, held by an org member
 *   GITHUB_ORG      the organization refusing the application
 */
const API = 'https://api.github.com';
const UA = 'github-oauth-app-restriction/1.0';

export const SSO_HEADER = 'x-github-sso';
export const ACCEPTED_SCOPES_HEADER = 'x-accepted-oauth-scopes';

export const TOKEN_PREFIXES = [
  ['github_pat_', 'fine-grained PAT'],
  ['ghp_', 'classic PAT'],
  ['gho_', 'OAuth user token'],
  ['ghu_', 'App user-to-server token'],
  ['ghs_', 'App installation token'],
  ['ghr_', 'App refresh token'],
];

/** Credentials this policy can actually govern. */
export const GOVERNED_BY_OAUTH_POLICY = {
  'OAuth user token': true,
  unknown: true,
  'classic PAT': false,
  'fine-grained PAT': false,
  'App user-to-server token': false,
  'App installation token': false,
  'App refresh token': false,
};

/** Corroboration only. GitHub prose can be reworded without warning. */
export const RESTRICTION_PHRASES = [
  'oauth app access restrictions',
  'oauth application access restrictions',
  'third-party application',
  'has not been granted access',
];

/** Authenticated requests this run spends. The anonymous read is separate. */
export function readCost() {
  return 3;
}

/** Name the credential from its prefix. Pure. */
export function tokenKind(token) {
  const value = String(token ?? '').trim();
  for (const [prefix, name] of TOKEN_PREFIXES) {
    if (value.startsWith(prefix)) return name;
  }
  return 'unknown';
}

/** Can this policy apply to this credential at all. Pure. */
export function governed(kind) {
  if (GOVERNED_BY_OAUTH_POLICY[kind] ?? true) {
    return [true, `this policy governs tokens issued by an OAuth App, and a ${kind} is one.`];
  }
  return [false, `a ${kind} is not issued by an OAuth App, so this policy does `
    + 'not govern it. A refusal here has another cause and another note.'];
}

/** Score the refusal prose. Pure. [matched, phrase]. */
export function messageSignature(message) {
  const text = String(message ?? '').toLowerCase();
  for (const phrase of RESTRICTION_PHRASES) {
    if (text.includes(phrase)) return [true, phrase];
  }
  return [false, null];
}

/** The two-namespace reading. Pure. */
export function namespaceShape(personalStatus, orgStatus) {
  const personalOk = personalStatus === 200;
  const orgRefused = orgStatus === 403 || orgStatus === 404;
  if (personalOk && orgRefused) {
    return ['personal-ok-org-refused', 'the same token reads personal '
      + 'repositories and is refused on this organization, which is a gate '
      + 'around the organization rather than a problem with the credential.'];
  }
  if (!personalOk && orgRefused) {
    return ['refused-everywhere', 'the token is refused on personal repositories '
      + 'too, so this is the credential rather than any organization policy.'];
  }
  if (personalOk && !orgRefused) {
    return ['nothing-refused', 'both namespaces answered, so nothing is being '
      + 'restricted for this application today.'];
  }
  return ['unclassified-shape', 'the pair of reads does not match a shape this '
    + 'script knows how to name.'];
}

/** Compare a credentialled read against no credential at all. Pure. */
export function anonymousContrast(anonStatus, tokenStatus) {
  const refused = tokenStatus === 403 || tokenStatus === 404;
  if (anonStatus === 200 && refused) {
    return ['restricted-below-anonymous', 'this token is refused where no token '
      + 'at all succeeds, so it is being blocked rather than under-privileged.'];
  }
  if ((anonStatus === 403 || anonStatus === 404) && refused) {
    return ['private-to-everyone', 'an anonymous caller cannot see this listing '
      + 'either, so the contrast proves nothing here.'];
  }
  return ['no-contrast', 'the authenticated read succeeded, so there is nothing '
    + 'to contrast.'];
}

/** The verdict. Pure. [state, detail]. */
export function discriminate(shape, ssoForm, acceptedScopes, matched, kind) {
  const [ok] = governed(kind);
  if (ssoForm) {
    return ['saml-not-oauth-restriction', 'the refusal carries x-github-sso, so '
      + 'this is SAML enforcement and not an application policy.'];
  }
  if (acceptedScopes) {
    return ['scope-shaped-refusal', 'the refusal names the scopes it accepts in '
      + 'x-accepted-oauth-scopes, which an application restriction does not do.'];
  }
  if (shape === 'refused-everywhere') {
    return ['credential-problem', 'the token is refused in its own namespace, so '
      + 'no organization policy explains it.'];
  }
  if (shape === 'nothing-refused') {
    return ['not-restricted', 'this application is reaching the organization '
      + 'resources right now.'];
  }
  if (shape !== 'personal-ok-org-refused') {
    return ['undetermined', 'the reads do not form a shape this script will put '
      + 'a name to.'];
  }
  if (!ok) {
    return ['not-an-oauth-app-credential', 'the shape is right but the credential '
      + 'is not one this policy governs.'];
  }
  if (matched) {
    return ['oauth-app-restricted', 'this organization restricts which OAuth Apps '
      + 'may access its data and this application has not been approved. No '
      + 'scope, no reissued token and no other user account will change that.'];
  }
  return ['oauth-app-restricted-likely', 'the shape is exactly an application '
    + 'restriction and the wording did not match anything known, which happens '
    + 'when GitHub rewords a message.'];
}

/** Who can and cannot run this diagnosis. Pure. */
export function visibilityNote() {
  return 'the application author cannot see this policy from their side. This run '
    + 'needs a token issued to the app, held by a member of the organization.';
}

/** The sentence a reader has to act on. Pure. */
export function repair(state, org) {
  if (state === 'oauth-app-restricted' || state === 'oauth-app-restricted-likely') {
    return `an owner of ${org} approves the application in the organization `
      + 'third-party access settings. There is no API that grants it and this '
      + 'script does not ask for it. Structurally, a GitHub App is installed per '
      + 'account rather than approved by blanket policy.';
  }
  if (state === 'saml-not-oauth-restriction') {
    return 'authorize the credential through the URL in the x-github-sso header.';
  }
  if (state === 'scope-shaped-refusal') {
    return 'compare the accepted scopes against the ones the token holds.';
  }
  if (state === 'credential-problem') {
    return 'fix the credential first; no organization policy is in play.';
  }
  if (state === 'not-an-oauth-app-credential') {
    return 'find the gate that applies to this credential type.';
  }
  if (state === 'not-restricted') {
    return `nothing. This application is not being restricted by ${org}.`;
  }
  return 'report both statuses and the headers; this run reached no verdict.';
}

function headers(token) {
  const common = {
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
  return token ? { ...common, Authorization: `Bearer ${token}` } : common;
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const org = (process.env.GITHUB_ORG || "dummy-github-org");
  if (!token || !org) {
    console.error('set GITHUB_TOKEN (issued by the app) and GITHUB_ORG');
    process.exitCode = 2;
    return;
  }
  console.log(`read cost: ${readCost()} request(s) against the core hourly quota, `
    + 'plus 1 unauthenticated request against the separate anonymous bucket');

  const kind = tokenKind(token);
  const me = await fetch(`${API}/user`, { headers: headers(token) });
  const account = me.status === 200 ? (await me.json()).login : null;
  console.log(`credential: ${kind}, account=${account ?? 'unreadable'}`);

  const personal = await fetch(`${API}/user/repos?per_page=1`, { headers: headers(token) });
  console.log(`GET /user/repos -> ${personal.status}`);

  const orgListing = await fetch(`${API}/orgs/${org}/repos?per_page=1`,
    { headers: headers(token) });
  console.log(`GET /orgs/${org}/repos -> ${orgListing.status}`);

  const ssoForm = (orgListing.headers.get(SSO_HEADER) || '').split(';')[0].trim().toLowerCase();
  const accepted = orgListing.headers.get(ACCEPTED_SCOPES_HEADER);
  console.log(`${SSO_HEADER}: ${ssoForm || 'absent, and that absence is the finding'}`);

  let message = '';
  try {
    const body = await orgListing.json();
    message = body && typeof body === 'object' ? (body.message || '') : '';
  } catch { message = ''; }
  const [matched, phrase] = messageSignature(message);
  console.log(`message: ${matched ? 'matched the documented restriction wording'
    : 'did not match any known restriction wording'}`);

  const anon = await fetch(`${API}/orgs/${org}/repos?per_page=1`, { headers: headers(null) });
  console.log(`anonymous read of the same listing -> ${anon.status}`);
  const [contrastState, contrastDetail] = anonymousContrast(anon.status, orgListing.status);
  console.log(`${contrastState}: ${contrastDetail}`);

  const [shape, shapeDetail] = namespaceShape(personal.status, orgListing.status);
  console.log(`${shape}: ${shapeDetail}`);
  const [state, detail] = discriminate(shape, ssoForm, accepted, matched, kind);
  console.log(`${state}: ${detail}`);
  console.log(`visibility: ${visibilityNote()}`);
  console.log(`repair: ${repair(state, org)}`);

  console.log(JSON.stringify({
    organization: org,
    account,
    credential_kind: kind,
    personal_status: personal.status,
    org_status: orgListing.status,
    anonymous_status: anon.status,
    sso_header: ssoForm || null,
    accepted_scopes_header: accepted,
    message_matched: matched,
    message_phrase: phrase,
    shape,
    contrast: contrastState,
    state,
    detail,
    visibility: visibilityNote(),
    repair: repair(state, org),
  }, null, 2));
  process.exitCode = state.startsWith('oauth-app-restricted') ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
