import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  AFFILIATIONS, counted, hasNextPage, headerValue, isMember,
  orgEndpointExpectation, orgProbeReading, readCost, repair, reposInOrg,
  roleVerdict, ssoReading, tokenClassCaveat, verdict,
} from './github-outside-collaborator.mjs';

// Obviously fake and far shorter than any real credential.
const FINE = 'github_pat_FAKE';
const CLASSIC = 'ghp_FAKE';

const ORGS = [{ login: 'acme' }, { login: 'Other' }];
const REPOS = [
  { full_name: 'acme/payments', owner: { login: 'acme' } },
  { full_name: 'acme/billing', owner: { login: 'ACME' } },
  { full_name: 'elsewhere/thing', owner: { login: 'elsewhere' } },
];

const NEXT_LINK = '<https://api.github.com/user/repos?page=2>; rel="next", '
  + '<https://api.github.com/user/repos?page=9>; rel="last"';
const LAST_ONLY = '<https://api.github.com/user/repos?page=1>; rel="prev"';

test('the partition is the diagnosis', () => {
  const [state, detail] = roleVerdict(false, 3, 0);
  assert.equal(state, 'outside-collaborator');
  assert.match(detail, /No scope grants standing/);
});

test('each other arm of the sort sends you somewhere else', () => {
  assert.equal(roleVerdict(true, 0, 12)[0], 'organization-member');
  let [state, detail] = roleVerdict(true, 0, 0);
  assert.equal(state, 'member-with-no-implicit-repos');
  assert.match(detail, /base permission of none/);
  [state, detail] = roleVerdict(false, 0, 0);
  assert.equal(state, 'no-relationship');
  assert.match(detail, /removal rather than a role/);
});

test('an announced partial list overrides the whole sort', () => {
  const [state, detail] = verdict('outside-collaborator', 'sso-partial-results');
  assert.equal(state, 'membership-list-incomplete');
  assert.match(detail, /no membership answer from it can be trusted/);
  assert.equal(verdict('outside-collaborator', 'no-sso-header')[0], 'outside-collaborator');
});

test('the absence of the sso header is read and reported', () => {
  const [state, detail] = ssoReading({});
  assert.equal(state, 'no-sso-header');
  assert.match(detail, /The SAML note is about the case where GitHub does tell you\./);
  assert.equal(ssoReading({ 'X-GitHub-SSO': 'partial-results; organizations=1,2' })[0],
    'sso-partial-results');
  assert.equal(ssoReading({ 'x-github-sso': 'required; url=https://example' })[0],
    'sso-header-present');
});

test('membership and ownership comparisons are case insensitive', () => {
  assert.equal(isMember(ORGS, 'ACME'), true);
  assert.equal(isMember(ORGS, 'nope'), false);
  assert.equal(isMember([], 'acme'), false);
  assert.deepEqual(reposInOrg(REPOS, 'acme'), ['acme/payments', 'acme/billing']);
  assert.deepEqual(reposInOrg(REPOS, 'elsewhere'), ['elsewhere/thing']);
});

test('a count says when it is only a floor', () => {
  assert.equal(hasNextPage(NEXT_LINK), true);
  assert.equal(hasNextPage(LAST_ONLY), false);
  assert.equal(hasNextPage(null), false);
  assert.deepEqual(counted(['a', 'b'], true), [2, false, 'at least 2']);
  assert.deepEqual(counted(['a', 'b'], false), [2, true, '2']);
  assert.deepEqual(counted([], false), [0, true, '0']);
});

test('the endpoint that does not fail is named as the dangerous one', () => {
  const expectation = orgEndpointExpectation('outside-collaborator');
  assert.match(expectation['org-repos-listing'], /under-reports/);
  assert.match(expectation['members-and-teams'], /404 rather than 403/);
  assert.match(expectation['outside-collaborators-listing'], /organization read access/);
  assert.match(orgEndpointExpectation('organization-member')['members-and-teams'],
    /answer for a member/);
});

test('the pair of readings is the sentence for the ticket', () => {
  const [state, detail] = orgProbeReading(200, 404);
  assert.equal(state, 'repo-yes-org-no');
  assert.match(detail, /put in the ticket/);
  assert.equal(orgProbeReading(200, 200)[0], 'org-reachable');
  assert.equal(orgProbeReading(200, 403)[0], 'org-refused-not-hidden');
  assert.equal(orgProbeReading(200, null)[0], 'org-not-probed');
});

test('the documented fine grained gap can invert the answer', () => {
  const [state, detail] = tokenClassCaveat(FINE);
  assert.equal(state, 'fine-grained-gap');
  assert.match(detail, /outside or repository collaborator/);
  assert.equal(tokenClassCaveat(CLASSIC)[0], 'classic-token');
  assert.equal(tokenClassCaveat('')[0], 'class-not-recognised');
});

test('the repair offers two choices and takes neither', () => {
  const fix = repair('outside-collaborator', 'acme', 'dana-integration');
  assert.match(fix, /ask an owner of acme/);
  assert.match(fix, /work at repository scope/);
  assert.match(fix, /Nothing here invites anybody/);
  assert.match(repair('member-with-no-implicit-repos', 'acme', 'dana'),
    /default repository permission/);
});

test('the read cost and the affiliation names', () => {
  assert.equal(readCost(false), 3);
  assert.equal(readCost(true), 4);
  assert.deepEqual(AFFILIATIONS, ['owner', 'collaborator', 'organization_member']);
});

test('header reads survive whatever case the client gives them', () => {
  assert.equal(headerValue({ Link: 'x' }, 'link'), 'x');
  assert.equal(headerValue({ link: 'x' }, 'LINK'), 'x');
  assert.equal(headerValue(null, 'link'), null);
});
