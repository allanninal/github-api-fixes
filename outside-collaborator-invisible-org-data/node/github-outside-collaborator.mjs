/**
 * Tell an outside collaborator from a member with a narrow token.
 *
 * Read only. Four cheap GETs. Nothing is invited, added or promoted: making an
 * account an organization member is a decision about who is inside a company's
 * organization, and this script prints the request rather than making it.
 *
 * An outside collaborator holds specific repositories inside an organization
 * without being in the organization. No scope closes that gap, because a scope
 * bounds what a token may do on the account's behalf rather than granting the
 * account a relationship.
 *
 * Environment:
 *   GITHUB_TOKEN      the token the failing integration holds
 *   GITHUB_ORG        the organization whose data is invisible
 *   GITHUB_ORG_PROBE  set to 1 to read one organization-level endpoint
 *   GITHUB_REPO       optional owner/name in that org to pair the probe with
 */
const API = 'https://api.github.com';
const UA = 'github-outside-collaborator/1.0';

/** The three ways GET /user/repos says an account reached a repository. */
export const AFFILIATIONS = ['owner', 'collaborator', 'organization_member'];

/** REST requests this run will spend. Pure. */
export function readCost(withOrgProbe) {
  return 3 + (withOrgProbe ? 1 : 0);
}

/** Case-insensitive header read against a plain object. Pure. */
export function headerValue(headers, name) {
  if (!headers || typeof headers !== 'object') return null;
  const wanted = String(name).toLowerCase();
  for (const key of Object.keys(headers)) {
    if (key.toLowerCase() === wanted) return headers[key];
  }
  return null;
}

/** Is there another page after this one. Pure. Turns a count into a floor. */
export function hasNextPage(linkHeader) {
  for (const part of String(linkHeader ?? '').split(',')) {
    if (part.includes('rel="next"')) return true;
    if (part.split(' ').join('').includes('rel="next"')) return true;
  }
  return false;
}

/** Is this response's shortness announced. Pure. [state, detail]. */
export function ssoReading(headers) {
  const value = headerValue(headers, 'x-github-sso');
  if (!value) {
    return ['no-sso-header', 'no partial-results header accompanied this list, '
      + 'so nothing was announced as withheld. The SAML note is about the case '
      + 'where GitHub does tell you.'];
  }
  if (String(value).includes('partial-results')) {
    return ['sso-partial-results', 'this list is explicitly incomplete: GitHub '
      + 'withheld organizations this token is not SSO-authorized for and said '
      + 'so in the header. That is a different note, and any membership '
      + 'conclusion from this list is unsafe.'];
  }
  return ['sso-header-present', 'an SSO header came back without a '
    + 'partial-results marker. Nothing is stated as withheld, but treat the '
    + 'list with care.'];
}

/** Does GET /user/orgs list this organization. Pure. */
export function isMember(orgs, org) {
  const wanted = String(org ?? '').toLowerCase();
  for (const entry of orgs || []) {
    if (entry && String(entry.login ?? '').toLowerCase() === wanted) return true;
  }
  return false;
}

/** Full names of the repositories in this organization. Pure. */
export function reposInOrg(repos, org) {
  const wanted = String(org ?? '').toLowerCase();
  const out = [];
  for (const repo of repos || []) {
    if (!repo || typeof repo !== 'object') continue;
    const owner = String((repo.owner && repo.owner.login) || '').toLowerCase();
    if (owner === wanted) out.push(repo.full_name);
  }
  return out;
}

/** A count, honest about being a floor. Pure. [count, exact, phrase]. */
export function counted(names, morePages) {
  const total = (names || []).length;
  if (morePages) return [total, false, `at least ${total}`];
  return [total, true, String(total)];
}

/** Which relationship the account has. Pure. [state, detail]. */
export function roleVerdict(member, collaboratorCount, memberAffiliatedCount) {
  if (member && memberAffiliatedCount > 0) {
    return ['organization-member', "the organization is in this account's "
      + 'membership list and its repositories arrive under organization_member. '
      + 'Whatever is failing, it is not this.'];
  }
  if (member && memberAffiliatedCount === 0) {
    return ['member-with-no-implicit-repos', 'the account is a member and '
      + 'reaches no repository through that membership. That is what a base '
      + 'permission of none looks like organization-wide, and it has its own note.'];
  }
  if (!member && collaboratorCount > 0) {
    return ['outside-collaborator', 'repositories inside the organization, no '
      + 'standing in the organization. No scope grants standing, which is why '
      + 'widening the token changes nothing.'];
  }
  return ['no-relationship', 'not a member and no repositories in this '
    + 'organization reachable as a collaborator. An account that used to have '
    + 'access and now has none is a removal rather than a role, and that has '
    + 'its own note.'];
}

/** What organization endpoints will do for this role. Pure. */
export function orgEndpointExpectation(role) {
  if (role === 'organization-member') {
    return {
      'members-and-teams': 'answer for a member',
      'org-repos-listing': 'returns the repositories a member may see',
      'outside-collaborators-listing': 'needs organization read access',
    };
  }
  return {
    'members-and-teams': 'refuse a non-member, and 404 rather than 403 so '
      + 'nothing is confirmed to exist',
    'org-repos-listing': 'answers 200 and returns the public repositories only. '
      + 'This does not fail; it under-reports, with no header and no error.',
    'outside-collaborators-listing': 'names this condition outright and needs '
      + 'organization read access, which this account does not have',
  };
}

/** A documented gap that can invert the diagnosis. Pure. [state, detail]. */
export function tokenClassCaveat(token) {
  const value = String(token ?? '').trim();
  if (value.startsWith('github_pat_')) {
    return ['fine-grained-gap', 'GitHub documents, among the things '
      + 'fine-grained tokens cannot yet do, contributing to repositories where '
      + 'the user is an outside or repository collaborator. If a classic token '
      + 'works where this one does not, that inversion is evidence of the role '
      + 'rather than a bug in your code.'];
  }
  if (value.startsWith('ghp_')) {
    return ['classic-token', 'a classic token is not subject to the documented '
      + 'fine-grained gap for outside collaborators, so a difference between '
      + 'the two classes is worth testing before blaming anything else.'];
  }
  return ['class-not-recognised', 'the credential class could not be named from '
    + 'its prefix, so the fine-grained caveat cannot be applied either way.'];
}

/** One repository read against one organization read. Pure. [state, detail]. */
export function orgProbeReading(repoStatus, orgStatus) {
  const repo = (repoStatus === null || repoStatus === undefined) ? null : Number(repoStatus);
  const org = (orgStatus === null || orgStatus === undefined) ? null : Number(orgStatus);
  if (org === null) {
    return ['org-not-probed', 'no organization endpoint was probed, so the '
      + 'partition is the only evidence here.'];
  }
  if (repo === 200 && org === 404) {
    return ['repo-yes-org-no', 'a repository in the organization answers and an '
      + 'organization endpoint does not. That pair is the sentence to put in '
      + 'the ticket.'];
  }
  if ([200, 204].includes(org)) {
    return ['org-reachable', 'the organization endpoint answered, so membership '
      + 'is not what is missing.'];
  }
  if ([401, 403].includes(org)) {
    return ['org-refused-not-hidden', 'a refusal rather than a 404 points at a '
      + 'credential or a policy rather than at membership. Sort that first.'];
  }
  return ['org-probe-inconclusive', 'the pair of statuses does not describe a '
    + 'membership problem.'];
}

/** The finding, in one state. Pure. [state, detail]. */
export function verdict(role, ssoState) {
  if (ssoState === 'sso-partial-results') {
    return ['membership-list-incomplete', 'the organization list this conclusion '
      + 'would rest on is explicitly partial, so no membership answer from it '
      + 'can be trusted. Authorize the token for SSO and re-run.'];
  }
  return [role, role ? 'this is the relationship the readings describe.'
    : 'no relationship could be determined.'];
}

/** The sentence a reader has to act on. Pure. Nothing here invites anybody. */
export function repair(state, org, login) {
  if (state === 'outside-collaborator') {
    return `either ask an owner of ${org} to add ${login} as a member with an `
      + 'appropriate role, which is a change to who is inside that '
      + 'organization, or drop the organization-level calls and work at '
      + "repository scope where this account's access actually is. Nothing here "
      + 'invites anybody.';
  }
  if (state === 'member-with-no-implicit-repos') {
    return "read the organization's default repository permission before "
      + 'anything else; an organization-wide default of none produces exactly '
      + 'this and has its own note.';
  }
  if (state === 'no-relationship') {
    return 'find out whether this account was removed from the organization '
      + 'rather than never added. A removal leaves a healthy token with no '
      + 'access at all.';
  }
  if (state === 'membership-list-incomplete') {
    return "authorize this token for the organization's SSO and re-run. Until "
      + 'then the membership list is not evidence.';
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

function bagOf(response) {
  const bag = {};
  response.headers.forEach((value, key) => { bag[key] = value; });
  return bag;
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const org = (process.env.GITHUB_ORG || "dummy-github-org");
  if (!token || !org) {
    console.error('set GITHUB_TOKEN (read-only is enough) and GITHUB_ORG');
    process.exitCode = 2;
    return;
  }
  const withOrgProbe = (process.env.GITHUB_ORG_PROBE || "dummy-github-org-probe") === '1';
  const repoName = (process.env.GITHUB_REP || "dummy-github-rep")O || '';
  console.log(`read cost: ${readCost(withOrgProbe)} REST request(s) against the `
    + 'core hourly quota');

  const [classState, classDetail] = tokenClassCaveat(token);
  console.log(`${classState}: ${classDetail}`);

  const me = await fetch(`${API}/user`, { headers: headers(token) });
  const login = me.status === 200 ? (await me.json()).login : null;
  console.log(`identity: ${login || 'unreadable'}`);

  const orgsResponse = await fetch(`${API}/user/orgs?per_page=100`,
    { headers: headers(token) });
  const orgs = orgsResponse.status === 200 ? await orgsResponse.json() : [];
  const [ssoState, ssoDetail] = ssoReading(bagOf(orgsResponse));
  const member = isMember(orgs, org);
  console.log(`membership: ${org} is ${member ? '' : 'not '}in GET /user/orgs. ${ssoDetail}`);

  const partition = {};
  for (const affiliation of ['collaborator', 'organization_member']) {
    const response = await fetch(
      `${API}/user/repos?affiliation=${affiliation}&per_page=100`,
      { headers: headers(token) },
    );
    const body = response.status === 200 ? await response.json() : [];
    const names = reposInOrg(body, org);
    const more = hasNextPage(headerValue(bagOf(response), 'link'));
    const [count, exact, phrase] = counted(names, more);
    partition[affiliation] = { count, exact, phrase, names: names.slice(0, 20) };
  }
  console.log(`affiliation partition: ${partition.collaborator.phrase} repo(s) `
    + `in ${org} reached as collaborator, `
    + `${partition.organization_member.phrase} reached as organization_member`);

  let orgStatus = null;
  let repoStatus = null;
  if (withOrgProbe) {
    const probe = await fetch(`${API}/orgs/${org}/members?per_page=1`,
      { headers: headers(token) });
    orgStatus = probe.status;
    console.log(`org probe: GET /orgs/${org}/members?per_page=1 -> HTTP ${orgStatus}`);
  }
  if (repoName) {
    const probe = await fetch(`${API}/repos/${repoName}`, { headers: headers(token) });
    repoStatus = probe.status;
    console.log(`repo probe: GET /repos/${repoName} -> HTTP ${repoStatus}`);
  }
  const [probeState, probeDetail] = orgProbeReading(repoStatus, orgStatus);
  console.log(`${probeState}: ${probeDetail}`);

  const [role, roleDetail] = roleVerdict(member, partition.collaborator.count,
    partition.organization_member.count);
  console.log(`${role}: ${roleDetail}`);

  const expectation = orgEndpointExpectation(role);
  console.log(`quiet-failure-ahead: ${expectation['org-repos-listing']}`);

  const [state, detail] = verdict(role, ssoState);
  const fix = repair(state, org, login || 'this account');
  console.log(`repair: ${fix}`);

  console.log(JSON.stringify({
    organization: org,
    login,
    is_member: member,
    sso_state: ssoState,
    affiliation_partition: partition,
    org_probe_status: orgStatus,
    repo_probe_status: repoStatus,
    probe_state: probeState,
    token_class_state: classState,
    org_endpoint_expectation: expectation,
    state,
    detail,
    repair: fix,
  }, null, 2));
  process.exitCode = ['outside-collaborator', 'member-with-no-implicit-repos',
    'no-relationship', 'membership-list-incomplete'].includes(state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
