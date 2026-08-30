import { test } from 'node:test';
import assert from 'node:assert/strict';
import { coverage, expectedTotal } from './github-app-coverage-audit.mjs';

test('org total needs both halves', () => {
  assert.equal(expectedTotal({ public_repos: 40, total_private_repos: 100 }), 140);
  assert.equal(expectedTotal({ public_repos: 0, total_private_repos: 0 }), 0);
});

test('a missing private count yields no total at all', () => {
  assert.equal(expectedTotal({ public_repos: 40 }), null);
  assert.equal(expectedTotal({}), null);
  assert.equal(expectedTotal(null), null);
});

test('all repositories is the only good news', () => {
  const [state, detail] = coverage('all', 140, 140);
  assert.equal(state, 'all-repositories');
  assert.match(detail, /automatically/);
});

test('twelve of a hundred and forty names the gap and the share', () => {
  const [state, detail] = coverage('selected', 12, 140);
  assert.equal(state, 'partial');
  assert.match(detail, /12 of 140/);
  assert.match(detail, /128/);
  assert.match(detail, /9%/);
});

test('selected and complete is not the same as all', () => {
  const [state, detail] = coverage('selected', 140, 140);
  assert.equal(state, 'selected-complete');
  assert.match(detail, /coincidence/);
});

test('no org total means a count, not a coverage figure', () => {
  const [state, detail] = coverage('selected', 12, null);
  assert.equal(state, 'unmeasured');
  assert.match(detail, /not a coverage figure/);
});

test('seeing more than exists is reported rather than averaged away', () => {
  assert.equal(coverage('selected', 150, 140)[0], 'inconsistent');
});

test('an uninterpretable selection is never assumed complete', () => {
  assert.equal(coverage(null, 12, 140)[0], 'unknown-selection');
  assert.equal(coverage('some-new-value', 12, 140)[0], 'unknown-selection');
});
