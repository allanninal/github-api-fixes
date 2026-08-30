import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  classify, daysPending, findRequest, headerIsNotTheDiscriminator, oauthWording,
  probeShape, readCost, repair, tokenKind,
} from './github-pat-pending-approval.mjs';

const NOW = Date.parse('2026-08-31T12:00:00Z');
const PERSONAL_OK = [['user', 200], ['repositories', 200], ['issues', 200]];
const ORG_ALL_REFUSED = [['repositories', 403], ['issues', 403], ['members', 403]];
const ORG_ONE_FAMILY = [['repositories', 200], ['issues', 403], ['members', 200]];

test('an owner shaped failure refuses every family in one namespace', () => {
  const [shape, detail] = probeShape(PERSONAL_OK, ORG_ALL_REFUSED);
  assert.equal(shape, 'owner-shaped');
  assert.ok(detail.includes('the gate is the resource owner'));
  assert.equal(classify(shape, 'fine-grained PAT', false, false)[0], 'pending-org-approval');
});

test('an endpoint shaped failure follows one family everywhere', () => {
  const personal = [['user', 200], ['repositories', 200], ['issues', 403]];
  const [shape, detail] = probeShape(personal, ORG_ONE_FAMILY);
  assert.equal(shape, 'endpoint-shaped');
  assert.ok(detail.includes('issues'));
  assert.equal(classify(shape, 'fine-grained PAT', false, false)[0], 'permission-shaped');
});

test('one organization family is not enough to name a cause', () => {
  assert.equal(probeShape(PERSONAL_OK, [['repositories', 403]])[0], 'insufficient-evidence');
  assert.equal(classify('insufficient-evidence', 'fine-grained PAT', false, false)[0],
    'undetermined');
});

test('a failing personal namespace is a credential not a queue', () => {
  const dead = [['user', 403], ['repositories', 403], ['issues', 403]];
  assert.equal(probeShape(dead, ORG_ALL_REFUSED)[0], 'credential-shaped');
});

test('the neighbouring gates outrank the shape', () => {
  assert.equal(classify('owner-shaped', 'fine-grained PAT', true, false)[0], 'saml-enforcement');
  assert.equal(classify('owner-shaped', 'fine-grained PAT', false, true)[0],
    'oauth-app-restriction');
  assert.equal(oauthWording('has enabled OAuth App access restrictions'), true);
  assert.equal(oauthWording('Resource not accessible by personal access token'), false);
});

test('a classic token is never sent to the approval queue', () => {
  assert.equal(classify('owner-shaped', 'classic PAT', false, false)[0],
    'not-a-fine-grained-token');
});

test('the permissions header is stated not to be the discriminator', () => {
  assert.ok(headerIsNotTheDiscriminator().includes('never what the token holds'));
});

test('the repair prints the approval and forbids a second token', () => {
  const fix = repair('pending-org-approval', 'acme-corp');
  assert.ok(fix.includes('an owner of acme-corp approves the waiting request'));
  assert.ok(fix.includes('does not approve it and does not ask for it'));
  assert.ok(fix.includes('Do not create a replacement token'));
});

test('the pending request is matched on a public login', () => {
  const pending = [{
    id: 42, owner: { login: 'Dana' }, repository_selection: 'all',
    created_at: '2026-08-25T09:00:00Z',
  }];
  assert.equal(findRequest(pending, 'dana').id, 42);
  assert.equal(findRequest(pending, 'someone-else'), null);
  assert.equal(daysPending('2026-08-25T09:00:00Z', NOW), 6);
  assert.equal(daysPending(null, NOW), null);
});

test('the credential type comes from its prefix', () => {
  assert.equal(tokenKind('github_pat_x'), 'fine-grained PAT');
  assert.equal(tokenKind('nope'), 'unknown');
});

test('the run costs six reads or seven', () => {
  assert.equal(readCost(), 6);
  assert.equal(readCost(true), 7);
});
