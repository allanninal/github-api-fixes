/**
 * Say whether an account was removed from an organization by a 2FA rule.
 *
 * Read only. Four GETs. Re-inviting a removed member is a write and an
 * organization owner's decision, so this script establishes the removal from
 * readable state and prints the request instead of making it.
 *
 * Enabling required two-factor authentication removes non-compliant members
 * rather than refusing them. The token keeps working, the account stops being
 * a member, and every private repository in the organization answers 404.
 *
 * Environment:
 *   GITHUB_TOKEN    the token the failing integration holds
 *   GITHUB_ORG      the organization whose repositories 404
 *   GITHUB_LOGIN    optional, ask about this account instead of the token's own
 */
const API = 'https://api.github.com';
const UA = 'github-org-membership-lost/1.0';

/** The documented answers of GET /orgs/{org}/members/{username}. */
export const MEMBERSHIP_STATUS = {
  204: 'member',
  302: 'requester-not-a-member',
  404: 'not-a-member',
  403: 'membership-unreadable',
};

/** Requests this run will spend against the core quota. Pure. */
export function readCost() {
  return 4;
}

/** What one membership status means. Pure. [state, detail]. */
export function membershipState(status) {
  const code = Number(status) || 0;
  const state = MEMBERSHIP_STATUS[code] || 'unclear';
  if (state === 'member') {
    return [state, '204 means the account is a member of this organization.'];
  }
  if (state === 'requester-not-a-member') {
    return [state, 'the 302 says the account asking is not a member of the '
      + 'organization. Asking about yourself, that is the removal.'];
  }
  if (state === 'not-a-member') {
    return [state, '404 here means the requester is a member and the named '
      + 'account is not.'];
  }
  if (state === 'membership-unreadable') {
    return [state, '403 on the membership read itself. The credential reached '
      + 'GitHub and was refused; sort that refusal first.'];
  }
  return ['unclear', `HTTP ${status} is not one of the documented answers for `
    + 'this endpoint.'];
}

/** Which question the status code is about. Pure. [state, detail]. */
export function questionAnswered(followedRedirect) {
  if (followedRedirect) {
    return ['public-membership-instead', 'the client followed the redirect, so '
      + 'this answer came from the public members endpoint and is about whether '
      + 'membership is publicly listed. Send the call again with redirects off.'];
  }
  return ['membership', 'redirects were disabled, so the status describes '
    + 'membership rather than public membership.'];
}

/** Whether this account has 2FA on. Pure. true, false or null for unreadable. */
export function ownTwoFactor(userPayload) {
  if (!userPayload || typeof userPayload !== 'object') return null;
  if (!Object.prototype.hasOwnProperty.call(userPayload, 'two_factor_authentication')) {
    return null;
  }
  const value = userPayload.two_factor_authentication;
  return value === null || value === undefined ? null : Boolean(value);
}

/** Whether the org requires 2FA. Pure. true, false or null for unreadable. */
export function requirementState(orgPayload) {
  if (!orgPayload || typeof orgPayload !== 'object') return null;
  if (!Object.prototype.hasOwnProperty.call(orgPayload, 'two_factor_requirement_enabled')) {
    return null;
  }
  const value = orgPayload.two_factor_requirement_enabled;
  return value === null || value === undefined ? null : Boolean(value);
}

/** Is the organization in GET /user/orgs. Pure. Corroboration, never a finding. */
export function listedInOrgs(orgs, org) {
  const wanted = String(org ?? '').trim().toLowerCase();
  for (const entry of orgs || []) {
    const login = entry && typeof entry === 'object' ? entry.login : entry;
    if (String(login ?? '').trim().toLowerCase() === wanted) return true;
  }
  return false;
}

/** Turn three readings into one finding. Pure. [state, detail]. */
export function combine(membership, requirement, ownTwoFa) {
  const gone = ['requester-not-a-member', 'not-a-member'].includes(membership);
  if (gone && requirement === true) {
    return ['not-a-member-2fa-required', 'the account is not a member and the '
      + 'organization requires two-factor authentication. That is the cause and '
      + 'its motive. Removal and never having joined are indistinguishable '
      + 'through the API, so this is a finding to act on rather than a proof.'];
  }
  if (gone && (requirement === null || requirement === undefined)) {
    return ['not-a-member-motive-unreadable', 'the account is not a member and '
      + 'the 2FA requirement could not be read. Reading it needs organization '
      + 'access, and losing that access is what this finding is.'];
  }
  if (gone) {
    return ['not-a-member-no-requirement', 'the account is not a member and the '
      + 'organization does not require 2FA, so something else removed it. An '
      + 'owner can read the audit log; a read-only token cannot.'];
  }
  if (membership === 'member' && requirement === true && ownTwoFa === false) {
    return ['member-at-risk', 'still a member, the organization requires 2FA, '
      + 'and this account reports two-factor authentication off. That is a '
      + 'removal that has not happened yet.'];
  }
  if (membership === 'member' && requirement === true
      && (ownTwoFa === null || ownTwoFa === undefined)) {
    return ['member-compliance-unreadable', 'still a member of an organization '
      + 'that requires 2FA, and this token cannot read whether the account '
      + 'complies. The user scope is what exposes that field.'];
  }
  if (membership === 'member' && requirement === true) {
    return ['member-compliant', 'a member, the requirement is on, and this '
      + 'account has 2FA. Nothing here explains a 404.'];
  }
  if (membership === 'member') {
    return ['member-no-requirement', 'a member of an organization with no 2FA '
      + 'requirement. This note is not your problem; sort the 404 another way.'];
  }
  return ['membership-unreadable', 'the membership question was not answered, '
    + 'so nothing can be concluded about a removal.'];
}

/** What the integration is seeing, given the finding. Pure. */
export function symptom(state) {
  if (String(state).startsWith('not-a-member')) {
    return 'every private repository in the organization answers 404, not 403, '
      + 'because a non-member cannot see them at all. Public repositories keep '
      + 'answering, which is what makes the token look healthy.';
  }
  if (state === 'member-at-risk') {
    return 'nothing yet. The reads still work and will keep working until the '
      + 'requirement is enforced against this account.';
  }
  return 'nothing that this note explains.';
}

/** State the credential's health explicitly. Pure. [state, detail]. */
export function tokenHealth(status) {
  const code = Number(status) || 0;
  if (code === 200) {
    return ['healthy', 'GET /user answered 200, so the credential '
      + 'authenticates. Nothing about the token explains what follows, and this '
      + 'line exists to end that search early.'];
  }
  if (code === 401) {
    return ['rejected', '401 means the credential itself was not accepted, '
      + 'which is a different note. This one starts from a token that works.'];
  }
  return ['unclear', `GET /user answered ${status}, which is neither of the two `
    + 'cases this note starts from.'];
}

/** The request a human has to make. Pure. Nothing here is executed. */
export function repair(state, org, login) {
  if (String(state).startsWith('not-a-member')) {
    return `enable 2FA on ${login} and ask an owner of ${org} to re-invite it, `
      + 'or replace the machine account with a GitHub App installation, which is '
      + 'not a member and is unaffected by member 2FA policy. Nothing here '
      + 're-invites anybody.';
  }
  if (state === 'member-at-risk') {
    return `enable 2FA on ${login} now, before the requirement is enforced `
      + 'against it. Removal is silent when it comes.';
  }
  if (state === 'member-compliance-unreadable') {
    return 'read this with a token carrying the user scope, or check the '
      + "account's security settings directly, to confirm it complies.";
  }
  if (state === 'member-compliant') {
    return 'nothing on membership. Take the 404 to the repository-level causes '
      + 'instead.';
  }
  return 'answer the membership question first: send the members call with '
    + 'redirects disabled and read the status rather than the body.';
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
    console.error('set GITHUB_TOKEN (the failing token) and GITHUB_ORG');
    process.exitCode = 2;
    return;
  }
  console.log(`read cost: ${readCost()} request(s) against the core hourly quota`);

  const me = await fetch(`${API}/user`, { headers: headers(token) });
  const [health, healthDetail] = tokenHealth(me.status);
  console.log(`token: ${health} — ${healthDetail}`);
  if (health !== 'healthy') { process.exitCode = 2; return; }
  const payload = await me.json();
  const login = (process.env.GITHUB_LOGI || "dummy-github-logi")N || payload.login || 'unknown';

  // redirect: manual, because the 302 is the finding and following it answers
  // a question about public membership instead.
  const members = await fetch(`${API}/orgs/${org}/members/${login}`, {
    headers: headers(token), redirect: 'manual',
  });
  const [state, detail] = membershipState(members.status);
  console.log(`membership: GET /orgs/${org}/members/${login} -> HTTP ${members.status} `
    + '(redirects disabled)');
  console.log(`${state}: ${detail}`);
  const [asked, askedDetail] = questionAnswered(false);
  console.log(`question answered: ${asked}. ${askedDetail}`);

  const orgResponse = await fetch(`${API}/orgs/${org}`, { headers: headers(token) });
  const orgPayload = orgResponse.status === 200 ? await orgResponse.json() : {};
  const requirement = requirementState(orgPayload);
  console.log(`motive: two_factor_requirement_enabled=`
    + `${requirement === null ? 'unreadable' : requirement}`);

  const orgsResponse = await fetch(`${API}/user/orgs?per_page=100`, {
    headers: headers(token),
  });
  const orgs = orgsResponse.status === 200 ? await orgsResponse.json() : [];
  const listed = listedInOrgs(orgs, org);
  console.log(`corroboration: the organization is ${listed ? 'listed' : 'absent'} `
    + 'in GET /user/orgs, which without read:org only lists publicly-visible '
    + 'membership');

  const own = (process.env.GITHUB_LOGIN || "dummy-github-login") ? null : ownTwoFactor(payload);
  const [finding, findingDetail] = combine(state, requirement, own);
  console.log(`state: ${finding} — ${findingDetail}`);
  console.log(`symptom: ${symptom(finding)}`);
  console.log(`repair: ${repair(finding, org, login)}`);

  console.log(JSON.stringify({
    organization: org,
    login,
    token_health: health,
    membership_status: members.status,
    membership_state: state,
    question_answered: asked,
    two_factor_requirement_enabled: requirement,
    account_two_factor: own,
    listed_in_user_orgs: listed,
    state: finding,
    detail: findingDetail,
    symptom: symptom(finding),
    repair: repair(finding, org, login),
  }, null, 2));
  process.exitCode = (String(finding).startsWith('not-a-member')
    || finding === 'member-at-risk') ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
