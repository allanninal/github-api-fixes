import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  ESCAPED_NEWLINE, inspectPem, interpret, issuerForm, reconcile, repairFor,
  unwrap, usable,
} from './github-app-key-identity.mjs';

const FILLER = Buffer.from('x'.repeat(1200)).toString('base64');

/** An obviously fake PEM: a real label and a run of filler bytes. */
function pem(label, body = FILLER) {
  const rows = [];
  for (let i = 0; i < body.length; i += 64) {
    rows.push(body.slice(i, i + 64));
  }
  return `-----BEGIN ${label}-----\n${rows.join('\n')}\n-----END ${label}-----\n`;
}

test('the key github issues is recognised and fingerprinted', () => {
  const out = inspectPem(pem('RSA PRIVATE KEY'));
  assert.equal(out.state, 'pkcs1-rsa-key');
  assert.equal(out.label, 'RSA PRIVATE KEY');
  assert.equal(out.fingerprint.length, 16);
  assert.ok(out.der_bytes >= 500);
  assert.ok(usable(out.state));
});

test('a pkcs8 wrapper is the same key and is accepted', () => {
  assert.equal(inspectPem(pem('PRIVATE KEY')).state, 'pkcs8-key');
});

test('the fingerprint identifies the file and nothing else', () => {
  const one = inspectPem(pem('RSA PRIVATE KEY')).fingerprint;
  const again = inspectPem(pem('RSA PRIVATE KEY')).fingerprint;
  const other = inspectPem(pem('RSA PRIVATE KEY',
    Buffer.from('y'.repeat(1200)).toString('base64'))).fingerprint;
  assert.equal(one, again);
  assert.notEqual(one, other);
});

test('escaped newlines are the headline deployment fault', () => {
  const flattened = pem('RSA PRIVATE KEY').split('\n').join(ESCAPED_NEWLINE);
  const out = inspectPem(flattened);
  assert.equal(out.state, 'escaped-newlines');
  assert.match(repairFor(out.state), /backslash and n/);
});

test('a pem collapsed onto one line is told apart from an escaped one', () => {
  const collapsed = pem('RSA PRIVATE KEY').split('\n').join(' ');
  assert.equal(inspectPem(collapsed).state, 'single-line-pem');
});

test('the wrong kind of key is named rather than guessed at', () => {
  assert.equal(inspectPem(pem('OPENSSH PRIVATE KEY')).state, 'openssh-format');
  assert.equal(inspectPem(pem('PUBLIC KEY')).state, 'public-key-not-private');
  assert.equal(inspectPem(pem('EC PRIVATE KEY')).state, 'not-an-rsa-key');
  assert.equal(inspectPem(pem('CERTIFICATE')).state, 'certificate-not-key');
  assert.equal(inspectPem(pem('ENCRYPTED PRIVATE KEY')).state, 'encrypted-key');
  assert.equal(inspectPem(pem('DH PARAMETERS')).state, 'unknown-pem-label');
  for (const state of ['openssh-format', 'public-key-not-private', 'not-an-rsa-key']) {
    assert.ok(!usable(state));
  }
});

test('a truncated pem is caught before its body is read', () => {
  const cut = pem('RSA PRIVATE KEY').split('-----END')[0];
  assert.equal(inspectPem(cut).state, 'truncated-pem');
});

test('a body that is not base64 says so', () => {
  assert.ok(['body-not-base64', 'too-small-for-rsa']
    .includes(inspectPem(pem('RSA PRIVATE KEY', 'not base64 at all')).state));
});

test('something far too small to be an rsa key is rejected', () => {
  const small = Buffer.from('z'.repeat(64)).toString('base64');
  const out = inspectPem(pem('RSA PRIVATE KEY', small));
  assert.equal(out.state, 'too-small-for-rsa');
  assert.notEqual(out.fingerprint, null);
});

test('an absent key is a state and not a crash', () => {
  assert.equal(inspectPem('').state, 'no-key-present');
  assert.equal(inspectPem(null).state, 'no-key-present');
  assert.equal(inspectPem('just some text').state, 'not-a-pem');
});

test('a base64 wrapped pem is unwrapped rather than rejected', () => {
  const raw = pem('RSA PRIVATE KEY');
  const wrapped = Buffer.from(raw).toString('base64');
  const [text, wasWrapped] = unwrap(wrapped);
  assert.equal(wasWrapped, true);
  assert.equal(inspectPem(text).state, 'pkcs1-rsa-key');
  assert.deepEqual(unwrap(raw), [raw.trim(), false]);
});

test('the issuer claim is checked for shape only', () => {
  assert.equal(issuerForm('123456'), 'app-id');
  assert.equal(issuerForm('Iv23liABCDEfghij'), 'client-id');
  assert.equal(issuerForm('acme-deploy-bot'), 'unusable-issuer');
  assert.equal(issuerForm(''), 'no-issuer');
});

test('one decode message covers five causes and says so', () => {
  const [state, detail] = interpret(401, 'A JSON web token could not be decoded');
  assert.equal(state, 'signature-rejected');
  assert.match(detail, /another App/);
  assert.match(detail, /RS256/);
});

test('the neighbouring failures are handed off rather than absorbed', () => {
  assert.equal(interpret(200, null)[0], 'key-accepted');
  assert.equal(interpret(404, 'Integration not found')[0], 'issuer-does-not-resolve');
  assert.equal(interpret(401, "'Issued at' claim ('iat') is in the future")[0],
    'clock-problem-not-key');
  assert.equal(interpret(401, "'Expiration time' claim ('exp') is too far in the future")[0],
    'lifetime-problem-not-key');
  assert.equal(interpret(401, 'Bad credentials')[0], 'not-a-jwt');
  assert.equal(interpret(403, 'Resource not accessible by integration')[0], 'unrelated');
});

test('a working key for the wrong app is the finding with no error', () => {
  const app = {
    id: 654321, client_id: 'Iv23liZZZZ', slug: 'acme-staging-bot',
    name: 'Acme Staging Bot',
  };
  const [state, detail] = reconcile(app, 'acme-deploy-bot');
  assert.equal(state, 'authenticated-as-another-app');
  assert.match(detail, /staging key reaches production/);
  assert.equal(reconcile(app, 'acme-staging-bot')[0], 'identity-matches');
  assert.equal(reconcile(app, '654321')[0], 'identity-matches');
  assert.equal(reconcile(app, 'Iv23liZZZZ')[0], 'identity-matches');
  assert.equal(reconcile(app, null)[0], 'no-expectation-given');
  assert.equal(reconcile(null, 'acme')[0], 'no-app-body');
});
