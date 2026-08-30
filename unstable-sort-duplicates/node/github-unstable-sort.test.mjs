import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_DIRECTION, compareWalks, duplicatesWithin, evidence, normalize,
  parseLink, readCost, repair, sortKind, stableParams, verdict, walkRisk,
} from './github-unstable-sort.mjs';

test('sort keys are sorted into movers and non-movers', () => {
  assert.equal(sortKind('updated'), 'mutable');
  assert.equal(sortKind('PUSHED'), 'mutable');
  assert.equal(sortKind('comments'), 'mutable');
  assert.equal(sortKind('created'), 'immutable');
  assert.equal(sortKind('full_name'), 'immutable');
  assert.equal(sortKind('banana'), 'unknown');
  assert.equal(sortKind(null), 'unknown');
});

test('the risk has three outcomes, not two', () => {
  assert.equal(walkRisk('updated', 'desc')[0], 'skips-and-duplicates');
  assert.equal(walkRisk('updated', 'asc')[0], 'skips-and-duplicates');
  assert.equal(walkRisk('created', 'desc')[0], 'duplicates-only');
  assert.equal(walkRisk('created', 'asc')[0], 'append-only');
  assert.equal(walkRisk('banana', 'asc')[0], 'unknown');
  assert.equal(walkRisk('created', 'sideways')[0], 'unknown');
});

test('a missing direction is treated as the one that shifts', () => {
  assert.equal(DEFAULT_DIRECTION, 'desc');
  assert.equal(walkRisk('created')[0], 'duplicates-only');
});

test('only the mutable key can hide a record', () => {
  assert.match(walkRisk('created', 'desc')[1], /hidden/);
  assert.match(walkRisk('created', 'asc')[1], /neither skip/);
  assert.match(walkRisk('updated', 'desc')[1], /only one of them is visible/);
});

test('repeats inside one walk are found and deduplicated', () => {
  assert.deepEqual(duplicatesWithin([1, 2, 2, 3, 3, 3]), ['2', '3']);
  assert.deepEqual(duplicatesWithin([1, 2, 3]), []);
  assert.deepEqual(duplicatesWithin([]), []);
  assert.deepEqual(duplicatesWithin(null), []);
});

test('ids are compared as strings so two walks line up', () => {
  assert.deepEqual(normalize([1, '1', 2]), ['1', '1', '2']);
  const diff = compareWalks([1, 2, 3], ['1', '2', '4']);
  assert.deepEqual(diff.missing, ['3']);
  assert.deepEqual(diff.appeared, ['4']);
  assert.equal(diff.first_count, 3);
});

test('growth in an append-only walk is not a finding', () => {
  const diff = compareWalks([1, 2, 3], [1, 2, 3, 4]);
  assert.deepEqual(evidence('append-only', diff), []);
  assert.deepEqual(evidence('skips-and-duplicates', diff), ['4']);
});

test('a shifting window proves nothing from set differences', () => {
  const diff = compareWalks([1, 2, 3], [0, 1, 2]);
  assert.deepEqual(evidence('duplicates-only', diff), []);
  assert.deepEqual(evidence('append-only', diff), ['3']);
});

test('a record in one walk and not the other is the finding', () => {
  const [state, detail] = verdict('updated', 'desc', [1, 2, 3], [1, 2, 4]);
  assert.equal(state, 'proven-skips');
  assert.match(detail, /never returned/);
});

test('a repeat inside a walk is reported as the gentler failure', () => {
  const [state, detail] = verdict('created', 'desc', [1, 2, 2, 3], [1, 2, 3]);
  assert.equal(state, 'proven-duplicates');
  assert.match(detail, /Nothing was hidden/);
});

test('agreeing walks on a mutable sort are exposure, not a pass', () => {
  const [state, detail] = verdict('updated', 'desc', [1, 2, 3], [1, 2, 3]);
  assert.equal(state, 'exposed');
  assert.match(detail, /quiet window rather than a safe walk/);
});

test('the safe ordering comes back clean', () => {
  assert.equal(verdict('created', 'asc', [1, 2, 3], [1, 2, 3])[0], 'stable-walk');
  assert.equal(verdict('created', 'desc', [1, 2, 3], [1, 2, 3])[0], 'insertion-shift');
  assert.equal(verdict('banana', 'asc', [1], [1])[0], 'unknown');
});

test('a walk with no evidence still gets classified', () => {
  assert.equal(verdict('updated', 'desc')[0], 'exposed');
  assert.equal(verdict('created', 'asc')[0], 'stable-walk');
});

test('the repairs are different for skips and for duplicates', () => {
  assert.match(repair('proven-skips'), /sort=created&direction=asc/);
  assert.match(repair('exposed'), /since=/);
  assert.match(repair('proven-duplicates'), /deduplicate on id/);
  assert.match(repair('insertion-shift'), /Nothing is being lost/);
  assert.ok(repair('stable-walk').startsWith('nothing on the ordering'));
});

test('the printed repair is a request you can send', () => {
  assert.deepEqual(stableParams(), { sort: 'created', direction: 'asc', per_page: 100 });
  assert.equal(stableParams(50, '2026-01-01T00:00:00Z').since, '2026-01-01T00:00:00Z');
});

test('the walk follows the header rather than counting pages', () => {
  const header = '<https://api.github.com/repos/o/n/issues?labels=bug,ci&page=2>; rel="next", '
    + '<https://api.github.com/repos/o/n/issues?labels=bug,ci&page=9>; rel="last"';
  assert.deepEqual(Object.keys(parseLink(header)).sort(), ['last', 'next']);
  assert.deepEqual(parseLink(null), {});
});

test('two walks cost twice what one does', () => {
  assert.equal(readCost(3), 6);
  assert.equal(readCost(3, 1), 3);
  assert.equal(readCost(0), 0);
  assert.equal(readCost(null), 0);
});
