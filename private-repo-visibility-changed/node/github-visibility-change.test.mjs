import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  ANONYMOUS_CORE_LIMIT, BLIND_SCOPE, PRIVATE_SCOPE, blindSpot, classify,
  clientIsAnonymous, forkFallout, readCost, repair, scopeGap, scopeList,
  visibilityOf,
} from './github-visibility-change.mjs';

const PRIVATE_NOW = { private: true, visibility: 'private', forks_count: 37 };
const INTERNAL = { private: true, visibility: 'internal', forks_count: 0 };
const PUBLIC = { private: false, visibility: 'public', forks_count: 12 };

test('the pair of readings is the finding', () => {
  const [state, detail] = classify(404, 200, PRIVATE_NOW);
  assert.equal(state, 'went-private');
  assert.match(detail, /invisible without one/);
  assert.match(detail, /Deletion would answer 404 to both/);
});

test('404 to both readings is handed to the wider triage', () => {
  const [state, detail] = classify(404, 404, null);
  assert.equal(state, 'invisible-to-both');
  assert.match(detail, /wider 404 triage/);
  assert.match(repair(state), /wider 404 triage/);
});

test('a public repository is not reported as a transition', () => {
  assert.equal(classify(200, 200, PUBLIC)[0], 'still-public');
});

test('an anonymous success beside an authenticated failure blames the token', () => {
  const [state, detail] = classify(200, 401, null);
  assert.equal(state, 'token-is-the-problem');
  assert.match(detail, /expired or revoked/);
});

test('a redirect anywhere is a rename and a different note', () => {
  assert.equal(classify(301, 200, PRIVATE_NOW)[0], 'moved');
  assert.equal(classify(404, 301, null)[0], 'moved');
});

test('internal is private true and still not private', () => {
  assert.equal(visibilityOf(INTERNAL), 'internal');
  assert.equal(INTERNAL.private, true);
  const [state, detail] = classify(404, 200, INTERNAL);
  assert.equal(state, 'internal-visibility');
  assert.match(detail, /every member of the enterprise/);
  assert.match(repair(state), /membership/);
});

test('visibility falls back to the boolean but prefers the field', () => {
  assert.equal(visibilityOf({ private: true }), 'private');
  assert.equal(visibilityOf({ private: false }), 'public');
  assert.equal(visibilityOf({}), 'unreported');
  assert.equal(visibilityOf(null), 'unreported');
});

test('the anonymous bucket proves whether a client authenticated', () => {
  assert.equal(ANONYMOUS_CORE_LIMIT, 60);
  assert.equal(clientIsAnonymous(60), true);
  assert.equal(clientIsAnonymous(5000), false);
  assert.equal(clientIsAnonymous(null), null);
});

test('public_repo is blind rather than merely narrow', () => {
  const [state, detail] = scopeGap([BLIND_SCOPE], 'private');
  assert.equal(state, 'blind-scope');
  assert.match(detail, /as blind here as sending no token at all/);
  assert.match(repair('went-private', state), new RegExp(BLIND_SCOPE));
});

test('the repo scope covers it and points at the account instead', () => {
  const [state, detail] = scopeGap([PRIVATE_SCOPE, 'workflow'], 'private');
  assert.equal(state, 'scope-sufficient');
  assert.match(detail, /no grant on the repository/);
});

test('a fine grained token reports no scopes and needs permissions', () => {
  const [state, detail] = scopeGap(null, 'private');
  assert.equal(state, 'no-scopes-reported');
  assert.match(detail, /Metadata: Read/);
  assert.match(detail, /Contents: Read/);
});

test('scopes are not asked about a public repository', () => {
  assert.equal(scopeGap(['public_repo'], 'public')[0], 'not-applicable');
  assert.equal(scopeGap([], 'private')[0], 'scope-insufficient');
});

test('absent and empty scope headers are different readings', () => {
  assert.equal(scopeList(null), null);
  assert.deepEqual(scopeList(''), []);
  assert.deepEqual(scopeList('repo, workflow'), ['repo', 'workflow']);
});

test('the detached forks are reported as a second failure', () => {
  const note = forkFallout(PRIVATE_NOW);
  assert.match(note, /still public/);
  assert.equal(forkFallout(PUBLIC), null);
  assert.equal(forkFallout(INTERNAL), null);
});

test('the missing timestamp is stated rather than guessed', () => {
  assert.match(blindSpot(), /no visibility-changed timestamp/);
  assert.match(blindSpot(), /your own logs/);
});

test('an unsorted pair is left unsorted', () => {
  const [state, detail] = classify(403, 500, null);
  assert.equal(state, 'unclassified');
  assert.match(detail, /500/);
  assert.match(detail, /403/);
});

test('the run costs two billable reads', () => {
  assert.equal(readCost(), 2);
});
