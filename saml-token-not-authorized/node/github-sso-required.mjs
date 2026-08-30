/**
 * Tell a SAML refusal apart from every other 403, from one response header.
 *
 * Read only, and one promise beyond that: this script never authorizes
 * anything. Authorizing a credential against an organization that enforces
 * SAML is deliberately a human step taken in a browser, so this reads the
 * refusal, prints the URL a person has to visit, and stops.
 *
 * The two forms of x-github-sso mean opposite things. `required` arrives on a
 * response that returned nothing. `partial-results` arrives on a 200 that
 * returned most of something, which is a different note entirely.
 *
 * Environment:
 *   GITHUB_TOKEN         the credential being refused
 *   GITHUB_ORG           the organization login being refused
 *   GITHUB_WORKED_BEFORE set to 1 if this credential used to succeed here
 */
const API = 'https://api.github.com';
const UA = 'github-sso-required/1.0';

export const SSO_HEADER = 'x-github-sso';
export const FORM_REQUIRED = 'required';
export const FORM_PARTIAL = 'partial-results';

/** Longest prefixes first. */
export const TOKEN_PREFIXES = [
  ['github_pat_', 'fine-grained PAT'],
  ['ghp_', 'classic PAT'],
  ['gho_', 'OAuth user token'],
  ['ghu_', 'App user-to-server token'],
  ['ghs_', 'App installation token'],
  ['ghr_', 'App refresh token'],
];

/** Whether a click on the authorization URL can help this credential type. */
export const CLICK_HELPS = {
  'classic PAT': [true, 'a classic PAT is authorized per token, per organization, '
    + 'by a person. Reminting it wider cannot change this answer.'],
  'OAuth user token': [true, 'an OAuth token is authorized per token, per '
    + 'organization, by the person who granted it.'],
  'App user-to-server token': [true, 'a user-to-server token inherits the user of '
    + 'record SAML standing, so the same click applies to it.'],
  'fine-grained PAT': [false, 'a fine-grained PAT has no per-token SSO '
    + 'authorization page. Its access is settled at creation and by the '
    + 'organization token policy, so a refusal here is usually a token waiting '
    + 'for an owner to approve it.'],
  'App installation token': [false, 'an installation token is not subject to '
    + 'per-token SSO authorization at all. If one is refused, SAML is not why.'],
  'App refresh token': [false, 'a refresh token is not used against these '
    + 'endpoints; exchange it first.'],
  unknown: [false, 'the credential type could not be named from its prefix, so '
    + 'nothing here prices whether a click helps.'],
};

export const STABLE_SSO_URL = (org) => `https://github.com/orgs/${org}/sso`;

/** Requests this run will spend against the core quota. Pure. */
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

/** Split x-github-sso into a form and its parameters. Pure. */
export function parseSsoHeader(value) {
  const out = { form: null, url: null, organizations: [] };
  if (!value) return out;
  const parts = String(value).split(';').map((p) => p.trim()).filter(Boolean);
  if (parts.length === 0) return out;
  out.form = parts[0].toLowerCase();
  for (const part of parts.slice(1)) {
    const at = part.indexOf('=');
    if (at < 0) continue;
    const key = part.slice(0, at).trim().toLowerCase();
    const val = part.slice(at + 1).trim();
    if (key === 'url') out.url = val;
    else if (key === 'organizations') {
      out.organizations = val.split(',').map((i) => i.trim()).filter(Boolean);
    }
  }
  return out;
}

/** Classify one pair of organization reads plus the header. Pure. */
export function enforcementSignature(metaStatus, listingStatus, sso) {
  const form = (sso || {}).form;
  const refused = listingStatus === 403 || listingStatus === 404;
  if (form === FORM_PARTIAL) {
    return ['partial-results-not-a-refusal', 'the header carries the '
      + 'partial-results form, which arrives on a response that succeeded with '
      + 'organizations left out of it. Nothing was refused here.'];
  }
  if (refused && form === FORM_REQUIRED) {
    return ['sso-authorization-required', 'this organization enforces SAML single '
      + 'sign-on and this credential has not been authorized against it. The '
      + 'token is valid; the organization has not admitted it.'];
  }
  if (refused && metaStatus === 200) {
    return ['refused-without-sso-header', 'the organization is readable and the '
      + 'listing is not, but GitHub did not attribute the refusal to SAML.'];
  }
  if (refused) {
    return ['organization-unreadable', 'even the organization own record could '
      + 'not be read, so this may be a name that does not resolve.'];
  }
  if (form === FORM_REQUIRED) {
    return ['sso-required-on-a-success', 'the listing succeeded and still carried '
      + 'the required form. Another endpoint will refuse the same credential.'];
  }
  return ['no-refusal-to-explain', 'the listing succeeded and carried no SAML '
    + 'header, so this credential is authorized for this organization right now.'];
}

/** The address a person has to open. Pure. [url, source]. */
export function authorizeUrl(sso, org) {
  const fromHeader = (sso || {}).url;
  if (fromHeader) {
    return [fromHeader, 'from the x-github-sso header, and short-lived: treat it '
      + 'as good for about an hour'];
  }
  return [STABLE_SSO_URL(org), 'the stable organization address, because the '
    + 'refusal carried no URL of its own'];
}

/** Can a human authorization click change this answer. Pure. */
export function clickVerdict(kind) {
  return CLICK_HELPS[kind] || CLICK_HELPS.unknown;
}

/** First authorization, or a lapsed one. Pure. */
export function whichSsoNote(workedBefore) {
  if (workedBefore) {
    return ['session-lapse', 'this credential succeeded here before, so it was '
      + 'authorized once and the authorization has lapsed. The click is the same; '
      + 'what changes is that it will be needed again on a schedule.'];
  }
  return ['first-authorization', 'no prior success was reported, so treat this as '
    + 'a credential that has never been authorized for this organization.'];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state, org, url, kind, workedBefore) {
  const [helps] = clickVerdict(kind);
  if (state !== 'sso-authorization-required') {
    if (state === 'partial-results-not-a-refusal') {
      return 'read the withheld organization IDs out of the header and treat the '
        + 'response as incomplete. Nothing here needs authorizing.';
    }
    if (state === 'refused-without-sso-header') {
      return 'diff the scopes the refusal names against the ones the credential '
        + 'holds, and check whether the organization restricts the application.';
    }
    if (state === 'organization-unreadable') {
      return 'check the organization name, then read this again.';
    }
    return `nothing on SAML. This credential is admitted to ${org} today.`;
  }
  if (!helps) {
    return 'do not send anyone to the SSO page for this credential type. The '
      + 'refusal is real and SAML is not the explanation for it.';
  }
  const lead = `open ${url} in a browser and authorize this credential for ${org}. `
    + 'This script does not open it and must not: the click is the control.';
  if (workedBefore) {
    return `${lead} Expect to do it again whenever the SAML session lapses, and `
      + 'move anything unattended onto an App installation token.';
  }
  return `${lead} For anything unattended, prefer an App installation token: it `
    + 'belongs to an installation the organization already approved.';
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
    console.error('set GITHUB_TOKEN (read-only is enough) and GITHUB_ORG');
    process.exitCode = 2;
    return;
  }
  const workedBefore = (process.env.GITHUB_WORKED_BEFORE || "dummy-github-worked-before") === '1';

  console.log(`read cost: ${readCost()} request(s) against the core hourly quota`);

  const h = headers(token);
  const me = await fetch(`${API}/user`, { headers: h });
  const kind = tokenKind(token);
  const account = me.status === 200 ? (await me.json()).login : null;
  console.log(`token: ${kind}, account=${account ?? 'unreadable'}`);

  const meta = await fetch(`${API}/orgs/${org}`, { headers: h });
  console.log(`GET /orgs/${org} -> ${meta.status}`);
  const listing = await fetch(`${API}/orgs/${org}/repos?per_page=1`, { headers: h });
  console.log(`GET /orgs/${org}/repos -> ${listing.status}`);

  const raw = listing.headers.get(SSO_HEADER) || meta.headers.get(SSO_HEADER);
  const sso = parseSsoHeader(raw);
  console.log(`${SSO_HEADER}: form=${sso.form ?? 'absent'}`);

  const [state, detail] = enforcementSignature(meta.status, listing.status, sso);
  console.log(`${state}: ${detail}`);
  const [helps, clickDetail] = clickVerdict(kind);
  console.log(`credential: ${clickDetail}`);

  const [url, urlSource] = authorizeUrl(sso, org);
  const [historyState] = whichSsoNote(workedBefore);
  if (state === 'sso-authorization-required') {
    console.log(`authorization url: ${url} (${urlSource})`);
  }
  console.log(`repair: ${repair(state, org, url, kind, workedBefore)}`);

  console.log(JSON.stringify({
    organization: org,
    account,
    token_kind: kind,
    org_read_status: meta.status,
    listing_status: listing.status,
    sso_header: sso,
    state,
    detail,
    click_can_help: helps,
    authorization_url: state === 'sso-authorization-required' ? url : null,
    history_state: historyState,
    repair: repair(state, org, url, kind, workedBefore),
  }, null, 2));
  process.exitCode = state === 'sso-authorization-required' ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
