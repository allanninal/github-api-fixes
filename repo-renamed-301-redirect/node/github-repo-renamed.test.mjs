import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  PERMANENT, TEMPORARY, durableKey, extraRoundTrips, isPermanent, isRedirect,
  readCost, repair, repoFromLocation, sameRepo, verdict,
} from './github-repo-renamed.mjs';

const BY_ID = 'https://api.github.com/repositories/1300192';
const BY_NAME = 'https://api.github.com/repos/acme/core-api';

test('permanent and temporary redirects are kept apart', () => {
  assert.ok(isRedirect(301) && isPermanent(301));
  assert.ok(isRedirect(308) && isPermanent(308));
  assert.ok(isRedirect(302) && !isPermanent(302));
  assert.ok(isRedirect(307) && !isPermanent(307));
  assert.ok(!isRedirect(200));
  assert.ok(!isRedirect(null));
  assert.ok(!PERMANENT.some((c) => TEMPORARY.includes(c)));
});

test('the Location usually names an id rather than a name', () => {
  assert.deepEqual(repoFromLocation(BY_ID), ['id', '1300192']);
  assert.deepEqual(repoFromLocation(BY_NAME), ['full_name', 'acme/core-api']);
  assert.deepEqual(repoFromLocation('/repos/acme/core-api'), ['full_name', 'acme/core-api']);
  assert.equal(repoFromLocation('https://example.test/nothing'), null);
  assert.equal(repoFromLocation(null), null);
});

test('names are compared the way GitHub compares them', () => {
  assert.ok(sameRepo('Acme/Platform', 'acme/platform'));
  assert.ok(sameRepo(' acme/platform ', 'acme/platform'));
  assert.ok(!sameRepo('acme/platform', 'acme/core-api'));
  assert.ok(!sameRepo(null, 'acme/platform'));
});

test('a permanent redirect is the finding and names the target', () => {
  const [state, detail] = verdict('acme/platform-api', 301, BY_ID, 'acme/core-api');
  assert.equal(state, 'renamed-permanent');
  assert.match(detail, /1300192/);
  assert.match(detail, /acme\/core-api/);
});

test('a permanent redirect without a Location is still a finding', () => {
  const [state, detail] = verdict('acme/platform-api', 301, null, null);
  assert.equal(state, 'renamed-permanent');
  assert.match(detail, /no usable Location/);
});

test('a temporary redirect must not be written into a config', () => {
  const [state, detail] = verdict('acme/platform-api', 302, BY_NAME, null);
  assert.equal(state, 'moved-temporary');
  assert.match(detail, /change nothing/);
  assert.match(repair(state), /change nothing/);
});

test('a followed redirect is caught by the name that came back', () => {
  const [state, detail] = verdict('acme/platform-api', 200, null, 'acme/core-api');
  assert.equal(state, 'renamed-followed');
  assert.match(detail, /nobody was told/);
});

test('capitalisation is not a rename', () => {
  const [state, detail] = verdict('Acme/Platform', 200, null, 'acme/platform');
  assert.equal(state, 'case-only');
  assert.match(detail, /capitalisation/);
  assert.ok(repair(state).startsWith('nothing.'));
});

test('a matching name is not a finding', () => {
  assert.equal(verdict('acme/core-api', 200, null, 'acme/core-api')[0], 'current');
  assert.equal(repair('current'), 'nothing.');
});

test('a 404 is handed to the note that owns it', () => {
  const [state, detail] = verdict('acme/gone', 404, null, null);
  assert.equal(state, 'not-found');
  assert.match(detail, /not a rename/);
  assert.match(repair(state), /triage the 404/);
});

test('an unreadable probe is never reported as a rename', () => {
  assert.equal(verdict('acme/x', null, null, null)[0], 'unknown');
  assert.equal(verdict('acme/x', 500, null, null)[0], 'unknown');
  assert.equal(verdict('acme/x', 200, null, null)[0], 'unknown');
});

test('the durable key is what the repair is really about', () => {
  assert.deepEqual(durableKey({ id: 1300192, node_id: 'R_kgDOE', name: 'core-api' }),
    { id: 1300192, node_id: 'R_kgDOE' });
  assert.equal(durableKey({ name: 'core-api' }), null);
  assert.equal(durableKey(null), null);
});

test('a followed redirect doubles the requests on that path', () => {
  assert.equal(extraRoundTrips(1200), 1200);
  assert.equal(extraRoundTrips(0), 0);
  assert.equal(extraRoundTrips(-5), 0);
  assert.equal(extraRoundTrips(null), 0);
});

test('the two rename repairs both point at the id', () => {
  assert.match(repair('renamed-permanent'), /node_id/);
  assert.match(repair('renamed-followed'), /node_id/);
  assert.match(repair('renamed-followed'), /following a redirect silently/);
});

test('the cost is stated as the upper bound it is', () => {
  assert.equal(readCost(['a/b', 'c/d']), 4);
  assert.equal(readCost(['a/b']), 2);
  assert.equal(readCost([]), 0);
  assert.equal(readCost(null), 0);
});
