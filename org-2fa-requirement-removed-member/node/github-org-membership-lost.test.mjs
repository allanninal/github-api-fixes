import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  combine, listedInOrgs, membershipState, ownTwoFactor, questionAnswered,
  readCost, repair, requirementState, symptom, tokenHealth,
} from './github-org-membership-lost.mjs';

test('the redirect is the finding and not an error', () => {
  const [state, detail] = membershipState(302);
  assert.equal(state, 'requester-not-a-member');
  assert.match(detail, /that is the removal/);
  assert.equal(membershipState(204)[0], 'member');
  assert.equal(membershipState(404)[0], 'not-a-member');
  assert.equal(membershipState(418)[0], 'unclear');
});

test('following the redirect answers a different question', () => {
  const [state, detail] = questionAnswered(true);
  assert.equal(state, 'public-membership-instead');
  assert.match(detail, /publicly listed/);
  assert.equal(questionAnswered(false)[0], 'membership');
});

test('an absent requirement field is unreadable not false', () => {
  assert.equal(requirementState({}), null);
  assert.equal(requirementState({ two_factor_requirement_enabled: null }), null);
  assert.equal(requirementState({ two_factor_requirement_enabled: true }), true);
  assert.equal(requirementState({ two_factor_requirement_enabled: false }), false);
});

test('an absent two factor field does not invent a violation', () => {
  assert.equal(ownTwoFactor({ login: 'octobot' }), null);
  assert.equal(ownTwoFactor({ two_factor_authentication: false }), false);
  assert.equal(ownTwoFactor({ two_factor_authentication: true }), true);
  assert.equal(ownTwoFactor(null), null);
});

test('the removal and its motive are reported together', () => {
  const [state, detail] = combine('requester-not-a-member', true, null);
  assert.equal(state, 'not-a-member-2fa-required');
  assert.match(detail, /cause and its motive/);
  assert.match(detail, /indistinguishable/);
});

test('an unreadable motive is still a finding', () => {
  const [state, detail] = combine('requester-not-a-member', null, null);
  assert.equal(state, 'not-a-member-motive-unreadable');
  assert.match(detail, /losing that access is what this finding is/);
});

test('a removal with no requirement is sent to the audit log', () => {
  const [state, detail] = combine('not-a-member', false, null);
  assert.equal(state, 'not-a-member-no-requirement');
  assert.match(detail, /audit log/);
});

test('a member with 2fa off is flagged before anything breaks', () => {
  const [state, detail] = combine('member', true, false);
  assert.equal(state, 'member-at-risk');
  assert.match(detail, /has not happened yet/);
});

test('a compliant member is sent somewhere else', () => {
  assert.equal(combine('member', true, true)[0], 'member-compliant');
  assert.equal(combine('member', true, null)[0], 'member-compliance-unreadable');
  assert.equal(combine('member', false, true)[0], 'member-no-requirement');
  assert.equal(combine('membership-unreadable', true, true)[0], 'membership-unreadable');
});

test('the symptom is 404 and not 403', () => {
  const text = symptom('not-a-member-2fa-required');
  assert.match(text, /404, not 403/);
  assert.match(text, /Public repositories keep answering/);
  assert.match(symptom('member-at-risk'), /nothing yet/);
});

test('a healthy token is stated so the search can move on', () => {
  const [state, detail] = tokenHealth(200);
  assert.equal(state, 'healthy');
  assert.match(detail, /end that search early/);
  assert.equal(tokenHealth(401)[0], 'rejected');
});

test('user orgs is corroboration and matches case insensitively', () => {
  const orgs = [{ login: 'ACME' }, { login: 'other' }];
  assert.equal(listedInOrgs(orgs, 'acme'), true);
  assert.equal(listedInOrgs(orgs, 'missing'), false);
  assert.equal(listedInOrgs([], 'acme'), false);
});

test('the repair offers the change that cannot happen again', () => {
  const fix = repair('not-a-member-2fa-required', 'acme', 'octobot');
  assert.match(fix, /octobot/);
  assert.match(fix, /GitHub App installation/);
  assert.match(fix, /re-invites anybody/);
  assert.match(repair('member-at-risk', 'acme', 'octobot'),
    /before the requirement is enforced/);
});

test('the run costs four reads', () => {
  assert.equal(readCost(), 4);
});
