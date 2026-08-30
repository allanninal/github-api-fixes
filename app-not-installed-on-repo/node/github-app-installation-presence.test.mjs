import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  accountRoute, classify, creationOrder, parseIso, repairFor, splitRepo,
  visibility,
} from './github-app-installation-presence.mjs';

const JAN = parseIso('2026-01-01T00:00:00Z');
const MAR = parseIso('2026-03-02T00:00:00Z');

test('every shape of repository reference lands on the same pair', () => {
  assert.deepEqual(splitRepo('acme/reporting'), ['acme', 'reporting']);
  assert.deepEqual(splitRepo('https://github.com/acme/reporting'), ['acme', 'reporting']);
  assert.deepEqual(splitRepo('https://github.com/acme/reporting/'), ['acme', 'reporting']);
  assert.deepEqual(splitRepo('https://github.com/acme/reporting/pulls/12'), ['acme', 'reporting']);
  assert.deepEqual(splitRepo('https://api.github.com/repos/acme/reporting'), ['acme', 'reporting']);
  assert.deepEqual(splitRepo('git@github.com:acme/reporting.git'), ['acme', 'reporting']);
});

test('something that is not a repository reference is refused', () => {
  assert.equal(splitRepo('acme'), null);
  assert.equal(splitRepo(''), null);
  assert.equal(splitRepo(null), null);
  assert.equal(splitRepo('acme/repo with spaces'), null);
});

test('organizations and user accounts have different routes', () => {
  assert.equal(accountRoute('acme', 'Organization'), '/orgs/acme/installation');
  assert.equal(accountRoute('octocat', 'User'), '/users/octocat/installation');
  assert.equal(accountRoute('acme', null), '/orgs/acme/installation');
});

test('a public repository narrows the search to your own app', () => {
  const [state, detail] = visibility(200);
  assert.equal(state, 'public-repo');
  assert.match(detail, /your side of the request/);
});

test('a 404 without a credential is two answers and says so', () => {
  const [state, detail] = visibility(404);
  assert.equal(state, 'not-public-or-absent');
  assert.match(detail, /cannot separate those two/);
  assert.equal(visibility(500)[0], 'visibility-unknown');
});

test('installed on the account but not the repository is the headline', () => {
  const [state, detail] = classify(404, 200);
  assert.equal(state, 'installed-on-account-not-repo');
  assert.match(detail, /never selected/);
  assert.match(repairFor(state, 'selected'), /add this repository/);
});

test('not installed at all is a different repair and a different person', () => {
  const [state] = classify(404, 404);
  assert.equal(state, 'not-installed-on-account');
  assert.match(repairFor(state, null), /admin rights/);
});

test('installed here means the 404 came from somewhere else', () => {
  const [state, detail] = classify(200, 200);
  assert.equal(state, 'installed-on-this-repo');
  assert.match(detail, /about something else/);
});

test('a refused jwt is not reported as an absent installation', () => {
  assert.equal(classify(401, 404)[0], 'jwt-not-accepted');
  assert.equal(classify(404, 403)[0], 'jwt-not-accepted');
  assert.match(classify(401, 404)[1], /Fix the JWT first/);
});

test('an unrecognised pair gets no verdict', () => {
  assert.equal(classify(500, 200)[0], 'inconclusive');
});

test('a repository newer than the installation is a recurring cause', () => {
  const [state, detail] = creationOrder(MAR, JAN, 'selected');
  assert.equal(state, 'repo-created-after-installation');
  assert.match(detail, /60 day\(s\) after/);
  assert.match(detail, /Every repository created from now on/);
});

test('a repository older than the installation was left out by hand', () => {
  assert.equal(creationOrder(JAN, MAR, 'selected')[0], 'repo-predates-installation');
});

test('an installation covering everything makes the dates irrelevant', () => {
  const [state, detail] = creationOrder(MAR, JAN, 'all');
  assert.equal(state, 'selection-covers-everything');
  assert.match(detail, /automatically/);
});

test('missing inputs produce a named state rather than a guess', () => {
  assert.equal(creationOrder(null, JAN, 'selected')[0], 'creation-order-unknown');
  assert.equal(creationOrder(MAR, null, 'selected')[0], 'creation-order-unknown');
  assert.equal(creationOrder(MAR, JAN, null)[0], 'selection-unknown');
});

test('timestamps that cannot be read are null rather than an exception', () => {
  assert.notEqual(parseIso('2026-01-01T00:00:00Z'), null);
  assert.equal(parseIso('last thursday'), null);
  assert.equal(parseIso(''), null);
  assert.equal(parseIso(null), null);
});
