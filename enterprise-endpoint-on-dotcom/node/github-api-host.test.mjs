import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  DOTCOM_API_HOST, FAMILIES, GHES_REST_SUFFIX, agreement, contentIsHtml,
  familyFromMeta, familyFromUrl, hostOf, identityCheck, normaliseBase, readCost,
  repair, servedHostFromRoot, tokenShapeIsNoEvidence, verdict,
} from './github-api-host.mjs';

const DOTCOM_META = {
  verifiable_password_authentication: false,
  hooks: ['192.30.252.0/22'],
  api: ['192.30.252.0/22'],
};
const GHES_META = {
  verifiable_password_authentication: true,
  installed_version: '3.14.2',
  hooks: ['10.0.0.0/8'],
};
const DOTCOM_ROOT = {
  current_user_url: 'https://api.github.com/user',
  repository_url: 'https://api.github.com/repos/{owner}/{repo}',
};
const GHES_ROOT = { current_user_url: 'https://github.acme.internal/api/v3/user' };

// Obviously fake and far shorter than any real credential.
const FINE = 'github_pat_FAKE';

test('the three host families are read off the url', () => {
  assert.equal(familyFromUrl('https://api.github.com')[0], 'dotcom');
  assert.equal(familyFromUrl('https://github.acme.internal/api/v3')[0], 'enterprise-server');
  assert.equal(familyFromUrl('https://api.octocorp.ghe.com')[0],
    'enterprise-cloud-data-residency');
  for (const name of ['dotcom', 'enterprise-server', 'enterprise-cloud-data-residency']) {
    assert.ok(FAMILIES.includes(name));
  }
});

test('the missing api prefix is named as its own failure', () => {
  let [state, detail] = familyFromUrl('https://github.acme.internal');
  assert.equal(state, 'web-host-not-api');
  assert.ok(detail.includes(GHES_REST_SUFFIX));
  [state, detail] = familyFromUrl('https://github.com');
  assert.equal(state, 'web-host-not-api');
  assert.ok(detail.includes(DOTCOM_API_HOST));
  assert.equal(familyFromUrl('not a url')[0], 'unknown');
});

test('the graphql path is still the appliance', () => {
  assert.equal(familyFromUrl('https://github.acme.internal/api/graphql')[0],
    'enterprise-server');
  assert.equal(normaliseBase('https://api.github.com///'), 'https://api.github.com');
  assert.equal(hostOf('https://API.GitHub.com/user'), 'api.github.com');
  assert.equal(hostOf('nonsense'), null);
});

test('installed_version is the discriminator', () => {
  let [state, detail] = familyFromMeta(200, 'application/json', GHES_META);
  assert.equal(state, 'enterprise-server');
  assert.ok(detail.includes('3.14.2'));
  [state, detail] = familyFromMeta(200, 'application/json', DOTCOM_META);
  assert.equal(state, 'dotcom-or-enterprise-cloud');
  assert.match(detail, /cannot be separated here/);
});

test('html from an api base is the silent one', () => {
  const [state, detail] = familyFromMeta(200, 'text/html; charset=utf-8', null);
  assert.equal(state, 'web-host-not-api');
  assert.match(detail, /reports success/);
  assert.equal(contentIsHtml('text/html'), true);
  assert.equal(contentIsHtml('application/json'), false);
  assert.equal(familyFromMeta(401, 'application/json', null)[0], 'meta-unreadable');
  assert.equal(familyFromMeta(200, 'application/json', { unrelated: 1 })[0], 'meta-unreadable');
});

test('the root map names the host that actually answered', () => {
  const [host, detail] = servedHostFromRoot(GHES_ROOT);
  assert.equal(host, 'github.acme.internal');
  assert.match(detail, /current_user_url/);
  assert.equal(servedHostFromRoot(DOTCOM_ROOT)[0], 'api.github.com');
  assert.equal(servedHostFromRoot({})[0], null);
  assert.equal(servedHostFromRoot({ x: 1 })[0], null);
});

test('a dotcom base against an appliance is the headline', () => {
  const [state, detail] = agreement('dotcom', 'enterprise-server',
    'api.github.com', 'api.github.com');
  assert.equal(state, 'wrong-host-family');
  assert.match(detail, /different installations/);
});

test('an appliance base answered by something else is caught too', () => {
  assert.equal(
    agreement('enterprise-server', 'dotcom-or-enterprise-cloud',
      'github.acme.internal', 'github.acme.internal')[0],
    'wrong-host-family',
  );
});

test('a redirect is the reading configuration cannot give you', () => {
  const [state, detail] = agreement('dotcom', 'dotcom-or-enterprise-cloud',
    'api.github.com', 'api.ghe.example');
  assert.equal(state, 'served-elsewhere');
  assert.match(detail, /reading the configuration would never have caught/);
});

test('agreement reports agreement', () => {
  assert.equal(
    agreement('dotcom', 'dotcom-or-enterprise-cloud', 'api.github.com', 'api.github.com')[0],
    'agrees',
  );
  assert.equal(
    agreement('enterprise-server', 'meta-unreadable', 'github.acme.internal', null)[0],
    'host-unidentified',
  );
  assert.equal(
    agreement('web-host-not-api', 'web-host-not-api', 'github.com', null)[0],
    'no-api-prefix',
  );
});

test('a credential from the other installation is stated plainly', () => {
  let [state, detail] = identityCheck(401, null, null, 'dana', 'github.acme.internal');
  assert.equal(state, 'credential-not-of-this-host');
  assert.match(detail, /it is not a token at all/);
  [state, detail] = identityCheck(200, 'someone-else',
    'https://github.acme.internal/someone-else', 'dana', 'github.acme.internal');
  assert.equal(state, 'wrong-account');
  assert.match(detail, /different installation/);
  assert.equal(identityCheck(0, null, null, null, null)[0], 'not-checked');
  assert.equal(identityCheck(503, null, null, null, null)[0], 'identity-unreadable');
});

test('the identity passes when the account and the host agree', () => {
  assert.equal(
    identityCheck(200, 'dana', 'https://github.acme.internal/dana', 'dana',
      'github.acme.internal')[0],
    'identity-as-expected',
  );
  assert.equal(
    identityCheck(200, 'dana', 'https://github.com/dana', 'dana', 'api.github.com')[0],
    'identity-as-expected',
  );
});

test('a token prefix names a class and never an installation', () => {
  const [state, detail] = tokenShapeIsNoEvidence(FINE);
  assert.equal(state, 'class-known-host-unknown');
  assert.match(detail, /never the installation/);
  assert.equal(tokenShapeIsNoEvidence('')[0], 'class-unknown');
});

test('the verdict and the repair are about configuration', () => {
  assert.equal(verdict('wrong-host-family', 'not-checked')[0], 'wrong-installation');
  assert.equal(verdict('no-api-prefix', 'not-checked')[0], 'no-api-prefix');
  assert.equal(verdict('served-elsewhere', 'not-checked')[0], 'redirected-elsewhere');
  assert.equal(verdict('agrees', 'credential-not-of-this-host')[0],
    'credential-from-another-host');
  assert.equal(verdict('agrees', 'identity-as-expected')[0], 'host-as-configured');
  const fix = repair('wrong-installation', 'https://api.github.com');
  assert.match(fix, /set the base URL explicitly/);
  assert.match(fix, /letting a library default decide/);
  assert.match(repair('host-as-configured', 'x'), /startup assertion/);
});

test('the host check needs no credential', () => {
  assert.deepEqual(readCost(false), [2, 2]);
  assert.deepEqual(readCost(true), [3, 2]);
});
