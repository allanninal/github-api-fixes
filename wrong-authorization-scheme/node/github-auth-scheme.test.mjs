import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  checkPairing, credentialKind, explain401, looksLikeJwt, parseAuthorization,
} from './github-auth-scheme.mjs';

const FAKE_JWT = 'eyJhbG.eyJpc3M.sig';

test('a jwt is recognised by shape without being decoded', () => {
  assert.equal(looksLikeJwt(FAKE_JWT), true);
  assert.equal(looksLikeJwt('eyJhbG.eyJpc3M'), false);
  assert.equal(looksLikeJwt('eyJhbG..sig'), false);
  assert.equal(looksLikeJwt('not.a.jwt'), false);
  assert.equal(looksLikeJwt(''), false);
});

test('each prefix names its credential type', () => {
  assert.equal(credentialKind('ghp_fake'), 'classic-pat');
  assert.equal(credentialKind('gho_fake'), 'oauth-user-token');
  assert.equal(credentialKind('ghu_fake'), 'user-to-server-token');
  assert.equal(credentialKind('ghs_fake'), 'installation-token');
  assert.equal(credentialKind('ghr_fake'), 'refresh-token');
  assert.equal(credentialKind('github_pat_fk'), 'fine-grained-pat');
});

test('a jwt wins over the prefix table', () => {
  assert.equal(credentialKind(FAKE_JWT), 'app-jwt');
});

test('the unprefixed legacy shape is still recognised', () => {
  assert.equal(credentialKind('0'.repeat(40)), 'legacy-pat');
  assert.equal(credentialKind('something-else'), 'unknown');
  assert.equal(credentialKind(null), 'absent');
  assert.equal(credentialKind('   '), 'absent');
});

test('a bare value has no scheme', () => {
  assert.equal(parseAuthorization('ghp_fake').scheme, null);
  assert.equal(parseAuthorization('ghp_fake').hasCredential, true);
});

test('a scheme and a value are split on whitespace', () => {
  const parsed = parseAuthorization('Bearer  ghp_fake');
  assert.equal(parsed.scheme, 'Bearer');
  assert.equal(parsed.words, 2);
});

test('an absent header is not an empty one', () => {
  assert.equal(parseAuthorization(null).hasCredential, false);
  assert.equal(parseAuthorization('').hasCredential, false);
});

test('a jwt under the token word is the headline failure', () => {
  const [state, detail, repair] = checkPairing('token', 'app-jwt');
  assert.equal(state, 'jwt-with-token-scheme');
  assert.ok(detail.includes('Bearer'));
  assert.ok(repair.includes('Bearer'));
});

test('a jwt under bearer is fine', () => {
  assert.equal(checkPairing('Bearer', 'app-jwt')[0], 'bearer-ok');
});

test('the scheme word is read case insensitively', () => {
  assert.equal(checkPairing('bearer', 'app-jwt')[0], 'bearer-ok');
  assert.equal(checkPairing('TOKEN', 'app-jwt')[0], 'jwt-with-token-scheme');
});

test('a pat under the legacy word works and is still reported', () => {
  const [state, detail] = checkPairing('token', 'classic-pat');
  assert.equal(state, 'legacy-scheme-accepted');
  assert.ok(detail.includes('nothing is failing because of it today'));
});

test('a bare value is its own state', () => {
  assert.equal(checkPairing(null, 'classic-pat')[0], 'scheme-missing');
});

test('basic is sent to the other note', () => {
  assert.equal(checkPairing('Basic', 'classic-pat')[0], 'basic-scheme');
});

test('an unread scheme word is named', () => {
  const [state, detail] = checkPairing('OAuth', 'classic-pat');
  assert.equal(state, 'unknown-scheme');
  assert.ok(detail.includes('OAuth'));
});

test('a refresh token is not an api credential', () => {
  assert.equal(checkPairing('Bearer', 'refresh-token')[0], 'refresh-token-sent');
});

test('no credential is not a scheme problem', () => {
  assert.equal(checkPairing('Bearer', 'absent')[0], 'no-credential');
});

test('the specific messages beat the generic one', () => {
  assert.equal(explain401('A JSON web token could not be decoded.')[0], 'jwt-expected');
  assert.equal(explain401('Requires authentication')[0], 'nothing-arrived');
  assert.equal(explain401('Bad credentials')[0], 'received-and-refused');
});

test('an unfamiliar message is admitted rather than guessed', () => {
  assert.equal(explain401('Something else entirely')[0], 'unmapped-message');
  assert.equal(explain401(null)[0], 'unmapped-message');
});
