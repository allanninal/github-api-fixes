import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  audit, credentialParams, fingerprint, isCredential, redact, sameCredential,
  shapeOf, urlsIn, verdict,
} from './github-token-in-url.mjs';

const FAKE = 'ghp_FAKE0000000001';
const OTHER = 'ghs_FAKE0000000002';
const BASE = 'https://api.github.com/repos/acme/api/issues';

test('documented prefixes are named', () => {
  assert.equal(shapeOf(FAKE), 'classic-pat');
  assert.equal(shapeOf(OTHER), 'app-installation-token');
  assert.equal(shapeOf('github_pat_FAKE01'), 'fine-grained-pat');
  assert.equal(shapeOf('a'.repeat(40)), 'legacy-hex40');
});

test('a short value is not treated as a credential', () => {
  assert.equal(shapeOf('30'), 'short');
  assert.equal(shapeOf(''), 'short');
});

test('a fingerprint is short, stable and not the value', () => {
  const fp = fingerprint(FAKE);
  assert.ok(fp.startsWith('sha256:'));
  assert.equal(fp.length, 'sha256:'.length + 12);
  assert.equal(fp, fingerprint(FAKE));
  assert.ok(!fp.includes(FAKE));
});

test('two sightings of one value correlate', () => {
  assert.equal(sameCredential(fingerprint(FAKE), fingerprint(FAKE)), true);
  assert.equal(sameCredential(fingerprint(FAKE), fingerprint(OTHER)), false);
  assert.equal(sameCredential(null, fingerprint(FAKE)), false);
});

test('the named parameter is found', () => {
  const hits = credentialParams(`${BASE}?access_token=${FAKE}&state=open`);
  assert.equal(hits.length, 1);
  assert.equal(hits[0].param, 'access_token');
  assert.equal(hits[0].shape, 'classic-pat');
  assert.equal(hits[0].ignored_by_github, true);
});

test('a credential hiding under a harmless name is still found', () => {
  const hits = credentialParams(`${BASE}?key=${FAKE}`);
  assert.equal(hits.length, 1);
  assert.equal(hits[0].param, 'key');
  assert.equal(hits[0].ignored_by_github, false);
});

test('a commit sha is not reported as a legacy token', () => {
  assert.equal(shapeOf('a'.repeat(40)), 'legacy-hex40');
  assert.equal(isCredential('sha', 'a'.repeat(40)), false);
  assert.deepEqual(credentialParams(`${BASE}?sha=${'a'.repeat(40)}`), []);
});

test('a credential name beats the git object exemption', () => {
  assert.equal(isCredential('access_token', 'a'.repeat(40)), true);
});

test('ordinary parameters are left alone', () => {
  assert.deepEqual(credentialParams(`${BASE}?state=open&per_page=100`), []);
});

test('a url with no query is not a finding', () => {
  assert.deepEqual(credentialParams(BASE), []);
});

test('redaction keeps the request and drops the secret', () => {
  const scrubbed = redact(`${BASE}?access_token=${FAKE}&state=open`);
  assert.ok(!scrubbed.includes(FAKE));
  assert.match(scrubbed, /REDACTED/);
  assert.match(scrubbed, /state=open/);
  assert.match(scrubbed, /\/repos\/acme\/api\/issues/);
});

test('urls are pulled out of a log line', () => {
  const line = `10.0.0.1 - - "GET ${BASE}?access_token=${FAKE} HTTP/1.1" 200`;
  const found = urlsIn(line);
  assert.equal(found.length, 1);
  assert.ok(found[0].startsWith('https://api.github.com/'));
});

test('nothing the script prints contains the credential', () => {
  const findings = audit([['access.log:12', `${BASE}?access_token=${FAKE}`]]);
  const [state, detail] = verdict(findings, true, fingerprint(FAKE));
  const printed = JSON.stringify(findings) + state + detail;
  assert.ok(!printed.includes(FAKE));
  assert.match(printed, /sha256:/);
});

test('a live match demands revocation', () => {
  const findings = audit([['access.log:12', `${BASE}?access_token=${FAKE}`]]);
  const [state, detail] = verdict(findings, true, fingerprint(FAKE));
  assert.equal(state, 'live-credential-in-url');
  assert.match(detail, /Revoke it/);
  assert.match(detail, /anonymous/);
});

test('a match on a dead credential is historical', () => {
  const findings = audit([['access.log:12', `${BASE}?access_token=${FAKE}`]]);
  const [state, detail] = verdict(findings, false, fingerprint(FAKE));
  assert.equal(state, 'dead-credential-in-url');
  assert.match(detail, /historical/);
});

test('an unknown credential is assumed live', () => {
  const findings = audit([['access.log:12', `${BASE}?access_token=${OTHER}`]]);
  const [state, detail] = verdict(findings, true, fingerprint(FAKE));
  assert.equal(state, 'credential-in-url');
  assert.match(detail, /treat them as live/);
});

test('distinct credentials are counted separately', () => {
  const findings = audit([
    ['a', `${BASE}?access_token=${FAKE}`],
    ['b', `${BASE}?access_token=${FAKE}`],
    ['c', `${BASE}?access_token=${OTHER}`],
  ]);
  const [, detail] = verdict(findings, false, null);
  assert.match(detail, /3 occurrence\(s\)/);
  assert.match(detail, /2 distinct/);
});

test('a clean scan says so', () => {
  assert.equal(verdict([], true, fingerprint(FAKE))[0], 'no-credential-in-url');
});
