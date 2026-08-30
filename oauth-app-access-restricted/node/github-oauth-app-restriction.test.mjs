import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  anonymousContrast, discriminate, governed, messageSignature, namespaceShape,
  readCost, repair, tokenKind, visibilityNote,
} from './github-oauth-app-restriction.mjs';

const RESTRICTED = 'Although you appear to have the correct authorization '
  + 'credentials, the acme-corp organization has enabled OAuth App access restrictions.';

test('the shape is one token reading two namespaces', () => {
  assert.equal(namespaceShape(200, 403)[0], 'personal-ok-org-refused');
  assert.equal(namespaceShape(403, 403)[0], 'refused-everywhere');
  assert.equal(namespaceShape(200, 200)[0], 'nothing-refused');
});

test('a saml header outranks every other piece of evidence', () => {
  const [matched] = messageSignature(RESTRICTED);
  assert.equal(
    discriminate('personal-ok-org-refused', 'required', null, matched, 'OAuth user token')[0],
    'saml-not-oauth-restriction',
  );
});

test('an accepted scopes header outranks it too', () => {
  assert.equal(
    discriminate('personal-ok-org-refused', '', 'repo, read:org', true, 'OAuth user token')[0],
    'scope-shaped-refusal',
  );
});

test('the verdict survives github rewording the message', () => {
  const [matched, phrase] = messageSignature(RESTRICTED);
  assert.equal(matched, true);
  assert.equal(phrase, 'oauth app access restrictions');
  assert.equal(
    discriminate('personal-ok-org-refused', '', null, matched, 'OAuth user token')[0],
    'oauth-app-restricted',
  );
  const [silent] = messageSignature('Something entirely new was written here');
  assert.equal(silent, false);
  assert.equal(
    discriminate('personal-ok-org-refused', '', null, silent, 'OAuth user token')[0],
    'oauth-app-restricted-likely',
  );
});

test('a token refused below anonymous is blocked not underprivileged', () => {
  assert.equal(anonymousContrast(200, 403)[0], 'restricted-below-anonymous');
  assert.equal(anonymousContrast(404, 403)[0], 'private-to-everyone');
  assert.equal(anonymousContrast(200, 200)[0], 'no-contrast');
});

test('only an oauth app credential is governed by this policy', () => {
  assert.equal(governed('OAuth user token')[0], true);
  assert.equal(governed('App installation token')[0], false);
  assert.equal(
    discriminate('personal-ok-org-refused', '', null, true, 'App installation token')[0],
    'not-an-oauth-app-credential',
  );
});

test('the repair names a person and denies an api', () => {
  const fix = repair('oauth-app-restricted', 'acme-corp');
  assert.ok(fix.includes('an owner of acme-corp approves the application'));
  assert.ok(fix.includes('no API that grants it'));
  assert.ok(fix.includes('does not ask for it'));
});

test('the visibility limit is part of the output', () => {
  assert.ok(visibilityNote().includes('cannot see this policy'));
});

test('the credential type comes from its prefix', () => {
  assert.equal(tokenKind('gho_fake'), 'OAuth user token');
  assert.equal(tokenKind('nope'), 'unknown');
});

test('the anonymous read is not charged to core quota', () => {
  assert.equal(readCost(), 3);
});
