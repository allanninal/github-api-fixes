import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  classifyCeiling, entitled, isLowerBound, reachable, repair,
  selectionOf, shortfall, sustainableRepos, verdict,
} from './github-app-limit-ceiling.mjs';

const WIDE = { total_count: 400, repository_selection: 'all' };
const NARROW = { total_count: 9, repository_selection: 'selected' };

test('nothing scales below twenty of either kind', () => {
  assert.equal(entitled(0, 0), 5000);
  assert.equal(entitled(20, 20), 5000);
  assert.equal(entitled(19, 19), 5000);
});

test('repositories and users both add', () => {
  assert.equal(entitled(21, 0), 5050);
  assert.equal(entitled(0, 21), 5050);
  assert.equal(entitled(21, 21), 5100);
});

test('the cap binds outside Enterprise Cloud', () => {
  assert.equal(entitled(1000, 1000), 12500);
  assert.equal(entitled(400, null), 12500);
});

test('enterprise replaces the sum rather than extending it', () => {
  assert.equal(entitled(0, 0, true), 15000);
  assert.equal(entitled(5000, 5000, true), 15000);
});

test('an unknown user count makes the answer a floor', () => {
  assert.equal(entitled(30, null), entitled(30, 0));
  assert.ok(entitled(30, 40) > entitled(30, null));
  assert.ok(isLowerBound(null));
  assert.ok(!isLowerBound(0));
});

test('each ceiling has a name', () => {
  assert.equal(classifyCeiling(60), 'unauthenticated');
  assert.equal(classifyCeiling(5000), 'baseline');
  assert.equal(classifyCeiling(7200), 'scaled');
  assert.equal(classifyCeiling(12500), 'at-cap');
  assert.equal(classifyCeiling(15000), 'enterprise');
  assert.equal(classifyCeiling(null), 'unknown');
});

test('the installation view is read defensively', () => {
  assert.equal(selectionOf(NARROW), 'selected');
  assert.equal(selectionOf({ repository_selection: 'ALL ' }), 'all');
  assert.equal(selectionOf({}), 'unknown');
  assert.equal(selectionOf(null), 'unknown');
  assert.equal(reachable(WIDE), 400);
  assert.equal(reachable({ total_count: null }), null);
  assert.equal(reachable(null), null);
});

test('a small installation at five thousand is honest', () => {
  const [state, detail] = verdict(5000, 'all', 9);
  assert.equal(state, 'baseline');
  assert.match(detail, /repair is on the usage side/);
});

test('a narrow installation on a big account is the finding', () => {
  const [state, detail] = verdict(5000, 'selected', 9, 400);
  assert.equal(state, 'narrow-installation');
  assert.match(detail, /12500/);
  assert.match(detail, /selection is what is capping it/);
});

test('a scaled ceiling that matches its size is not a finding', () => {
  assert.equal(verdict(entitled(60, null), 'all', 60)[0], 'scaled');
});

test('the cap and enterprise are never reported as shortfalls', () => {
  assert.equal(verdict(12500, 'selected', 900, 4000)[0], 'at-cap');
  assert.equal(verdict(15000, 'all', 4000)[0], 'enterprise');
});

test('an anonymous ceiling is not an installation problem', () => {
  assert.equal(verdict(60, 'unknown', null, null, null, false, false)[0], 'unauthenticated');
});

test('a credential with no installation view is named as such', () => {
  const [state, detail] = verdict(5000, 'unknown', null, null, null, false, false);
  assert.equal(state, 'not-an-installation');
  assert.match(detail, /user or Actions credential/);
});

test('the shortfall never goes negative', () => {
  assert.equal(shortfall(12500, 5000), 0);
  assert.equal(shortfall(5000, 12500), 7500);
  assert.equal(shortfall(null, 12500), 0);
});

test('the budget divides the ceiling by the loop', () => {
  assert.equal(sustainableRepos(12500, 10), 1250);
  assert.equal(sustainableRepos(5000, 12), 416);
  assert.equal(sustainableRepos(5000, 0), null);
});

test('the repair for a real ceiling does not suggest widening', () => {
  assert.ok(!repair('baseline').includes('widen'));
  assert.match(repair('narrow-installation'), /widen the installation/);
});
