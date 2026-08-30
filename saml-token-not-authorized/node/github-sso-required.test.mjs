import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  FORM_PARTIAL, FORM_REQUIRED, authorizeUrl, clickVerdict, enforcementSignature,
  parseSsoHeader, readCost, repair, tokenKind, whichSsoNote,
} from './github-sso-required.mjs';

const REQUIRED_HEADER = 'required; url=https://github.com/orgs/acme-corp/sso'
  + '?authorization_request=AB12CD';
const PARTIAL_HEADER = 'partial-results; organizations=21955855,20582480';

test('the required form keeps its whole url', () => {
  const sso = parseSsoHeader(REQUIRED_HEADER);
  assert.equal(sso.form, FORM_REQUIRED);
  assert.ok(sso.url.endsWith('?authorization_request=AB12CD'));
});

test('the partial form is a different finding not a refusal', () => {
  const sso = parseSsoHeader(PARTIAL_HEADER);
  assert.equal(sso.form, FORM_PARTIAL);
  assert.deepEqual(sso.organizations, ['21955855', '20582480']);
  assert.equal(enforcementSignature(200, 200, sso)[0], 'partial-results-not-a-refusal');
});

test('an absent header parses without inventing a form', () => {
  assert.deepEqual(parseSsoHeader(null), { form: null, url: null, organizations: [] });
  assert.equal(parseSsoHeader('required').url, null);
});

test('the signature is a pair of reads not one status', () => {
  const sso = parseSsoHeader(REQUIRED_HEADER);
  assert.equal(enforcementSignature(200, 403, sso)[0], 'sso-authorization-required');
  assert.equal(enforcementSignature(200, 404, sso)[0], 'sso-authorization-required');
});

test('a misspelled org is never reported as saml', () => {
  assert.equal(enforcementSignature(404, 404, parseSsoHeader(null))[0],
    'organization-unreadable');
});

test('a refusal without the header is handed elsewhere', () => {
  assert.equal(enforcementSignature(200, 403, parseSsoHeader(null))[0],
    'refused-without-sso-header');
});

test('the url falls back to the address that never expires', () => {
  const [url, source] = authorizeUrl(parseSsoHeader(null), 'acme-corp');
  assert.equal(url, 'https://github.com/orgs/acme-corp/sso');
  assert.ok(source.includes('stable'));
});

test('an installation token is never sent to the sso page', () => {
  const [helps] = clickVerdict('App installation token');
  assert.equal(helps, false);
  const fix = repair('sso-authorization-required', 'acme-corp',
    'https://github.com/orgs/acme-corp/sso', 'App installation token', false);
  assert.ok(fix.includes('do not send anyone to the SSO page'));
});

test('the repair says the click belongs to a person', () => {
  const fix = repair('sso-authorization-required', 'acme-corp',
    'https://github.com/orgs/acme-corp/sso', 'classic PAT', false);
  assert.ok(fix.includes('does not open it and must not'));
});

test('a prior success points at the lapse note instead', () => {
  assert.equal(whichSsoNote(true)[0], 'session-lapse');
  assert.equal(whichSsoNote(false)[0], 'first-authorization');
});

test('the credential type comes from its prefix locally', () => {
  assert.equal(tokenKind('ghp_fake'), 'classic PAT');
  assert.equal(tokenKind('ghs_fake'), 'App installation token');
  assert.equal(tokenKind('nope'), 'unknown');
});

test('the run costs three reads', () => {
  assert.equal(readCost(), 3);
});
