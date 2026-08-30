import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  PUSH_DAYS_OBVIOUS, classify, daysBetween, divergence, forkChain, isFork,
  parseTs, quietAuditReasons, readCost, repair, upstreamOf,
} from './github-fork-or-upstream.mjs';

const FORK = {
  id: 904113,
  node_id: 'R_kgDONjA',
  fork: true,
  has_issues: false,
  open_issues_count: 0,
  forks_count: 0,
  stargazers_count: 41,
  pushed_at: '2025-01-14T09:22:10Z',
  default_branch: 'master',
  parent: { full_name: 'octo-org/platform-core' },
  source: { full_name: 'octo-org/platform-core' },
};
const FORK_OF_FORK = {
  ...FORK,
  parent: { full_name: 'acme/platform-core' },
  source: { full_name: 'octo-org/platform-core' },
};
const UPSTREAM = {
  id: 20221,
  fork: false,
  has_issues: true,
  open_issues_count: 9134,
  forks_count: 1875,
  stargazers_count: 12400,
  pushed_at: '2026-08-28T17:03:44Z',
  default_branch: 'main',
};

test('a fork is a separate repository and that is the finding', () => {
  assert.equal(isFork(FORK), true);
  const [state, detail] = classify(FORK);
  assert.equal(state, 'fork-as-canonical');
  assert.match(detail, /own issues, releases and branches/);
  assert.equal(classify(UPSTREAM)[0], 'canonical');
});

test('source is preferred over parent', () => {
  assert.equal(upstreamOf(FORK_OF_FORK), 'octo-org/platform-core');
  assert.equal(forkChain(FORK_OF_FORK).parent, 'acme/platform-core');
  const [state, detail] = classify(FORK_OF_FORK);
  assert.equal(state, 'fork-of-fork');
  assert.match(detail, /root of the network is octo-org\/platform-core/);
});

test('a repository with no upstream reports none', () => {
  assert.equal(upstreamOf(UPSTREAM), null);
  assert.deepEqual(forkChain({}), { parent: null, source: null });
});

test('id drift is checked before the fork question', () => {
  const [state, detail] = classify({ ...UPSTREAM, id: 88 }, 20221);
  assert.equal(state, 'id-drift');
  assert.match(detail, /20221/);
  assert.match(detail, /88/);
  assert.equal(classify(UPSTREAM, 20221)[0], 'canonical');
  assert.equal(classify(UPSTREAM, '')[0], 'canonical');
});

test('the gap is reported in units a person recognises', () => {
  const gaps = divergence(FORK, UPSTREAM);
  assert.deepEqual(gaps.stargazers_count,
    { fork: 41, upstream: 12400, difference: 12359 });
  assert.equal(gaps.open_issues_count.difference, 9134);
  assert.deepEqual(gaps.default_branch, { fork: 'master', upstream: 'main' });
  assert.equal(gaps.obvious, true);
});

test('a close copy is not reported as obvious', () => {
  const near = { ...FORK, stargazers_count: 12000, pushed_at: '2026-08-27T10:00:00Z' };
  const gaps = divergence(near, UPSTREAM);
  assert.equal(gaps.obvious, false);
  assert.ok(gaps.pushed_days_behind < PUSH_DAYS_OBVIOUS);
});

test('timestamps are parsed and differenced', () => {
  assert.notEqual(parseTs('2026-08-28T17:03:44Z'), null);
  assert.equal(parseTs('not a date'), null);
  assert.equal(daysBetween('2026-08-01T00:00:00Z', '2026-08-28T00:00:00Z'), 27);
  assert.equal(daysBetween('nope', '2026-08-28T00:00:00Z'), null);
});

test('the quiet symptoms are gathered under one cause', () => {
  const reasons = quietAuditReasons(FORK, 0).join(' ');
  assert.match(reasons, /issues are disabled/);
  assert.match(reasons, /no open issues/);
  assert.match(reasons, /no releases/);
  assert.match(reasons, /nothing has forked it/);
  assert.deepEqual(quietAuditReasons(UPSTREAM, 3), []);
});

test('disabled issues on a fork answers 410 not an empty list', () => {
  assert.match(quietAuditReasons(FORK).join(' '), /410/);
});

test('the repair names the upstream and the id to store', () => {
  const fix = repair('fork-as-canonical', FORK);
  assert.match(fix, /octo-org\/platform-core/);
  assert.match(fix, /store its id/);
  const drift = repair('id-drift', { ...UPSTREAM, id: 88 }, 20221);
  assert.match(drift, /88/);
  assert.match(drift, /20221/);
  assert.match(repair('canonical', UPSTREAM), /survives a rename/);
});

test('the run costs two reads by default', () => {
  assert.equal(readCost(), 2);
  assert.equal(readCost(false), 1);
  assert.equal(readCost(true, true), 4);
});
