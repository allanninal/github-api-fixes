import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseSso, verdict } from './github-sso-partial-results.mjs';

test('partial form yields the withheld ids', () => {
  const sso = parseSso('partial-results; organizations=21955855,20582480');
  assert.equal(sso.kind, 'partial-results');
  assert.deepEqual(sso.organizations, ['21955855', '20582480']);
  assert.equal(sso.url, null);
});

test('required form yields the authorization url', () => {
  const sso = parseSso('required; url=https://github.com/orgs/acme/sso?x=1');
  assert.equal(sso.kind, 'required');
  assert.equal(sso.url, 'https://github.com/orgs/acme/sso?x=1');
});

test('absent and blank headers are the same nothing', () => {
  assert.equal(parseSso(null).kind, 'none');
  assert.equal(parseSso('').kind, 'none');
  assert.equal(parseSso('   ').kind, 'none');
});

test('an unrecognised value is never read as absence', () => {
  const sso = parseSso('some-future-directive; organizations=1');
  assert.equal(sso.kind, 'unknown');
  assert.equal(verdict(200, sso, 4)[0], 'unreadable');
});

test('a 200 with partial results is a failure', () => {
  const sso = parseSso('partial-results; organizations=21955855,20582480');
  const [state, detail] = verdict(200, sso, 4);
  assert.equal(state, 'partial');
  assert.match(detail, /4 organization\(s\)/);
  assert.match(detail, /2 withheld/);
  assert.match(detail, /21955855/);
});

test('a 403 with the required form is the loud version', () => {
  const sso = parseSso('required; url=https://github.com/orgs/acme/sso');
  const [state, detail] = verdict(403, sso, 0);
  assert.equal(state, 'authorization-required');
  assert.match(detail, /orgs\/acme\/sso/);
});

test('a 403 without the header is not an sso problem', () => {
  const [state, detail] = verdict(403, parseSso(null), 0);
  assert.equal(state, 'forbidden');
  assert.match(detail, /read:org/);
});

test('a clean 200 is complete', () => {
  const [state, detail] = verdict(200, parseSso(null), 6);
  assert.equal(state, 'complete');
  assert.match(detail, /6 organization\(s\)/);
});

test('the header outranks the status code', () => {
  const sso = parseSso('partial-results; organizations=99');
  assert.equal(verdict(200, sso, 1)[0], 'partial');
  assert.equal(verdict(500, sso, 1)[0], 'partial');
});
