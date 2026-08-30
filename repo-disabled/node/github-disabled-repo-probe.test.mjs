import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_PROBES, EMPTY_REPOSITORY, aggregateImpact, aggregateSafety,
  explainsSubresource, isDisabled, isRealZero, platformState, probeVerdict,
  readCost, remedyOwner, repair,
} from './github-disabled-repo-probe.mjs';

const DISABLED = { full_name: 'acme/payments-legacy', disabled: true, archived: false };
const ARCHIVED = { full_name: 'acme/legacy-billing', disabled: false, archived: true };
const BOTH = { full_name: 'acme/gone', disabled: true, archived: true };
const ACTIVE = { full_name: 'acme/platform-api', disabled: false, archived: false };

const GHOST_PROBES = [
  { path: '/branches', status: 404 },
  { path: '/commits', status: 404 },
  { path: '/contributors', status: 404 },
  { path: '/languages', status: 200 },
];
const ALL_FINE = [
  { path: '/branches', status: 200 }, { path: '/commits', status: 200 },
];
const NEW_REPO = [
  { path: '/branches', status: 200 }, { path: '/commits', status: EMPTY_REPOSITORY },
];

test('the two booleans make four platform states', () => {
  assert.equal(platformState(DISABLED), 'disabled');
  assert.equal(platformState(ARCHIVED), 'archived');
  assert.equal(platformState(BOTH), 'disabled-and-archived');
  assert.equal(platformState(ACTIVE), 'active');
  assert.equal(platformState(null), 'unknown');
  assert.equal(isDisabled('disabled-and-archived'), true);
  assert.equal(isDisabled('archived'), false);
});

test('a failure is only explained when the state explains it', () => {
  assert.equal(explainsSubresource('disabled', 404)[0], true);
  assert.equal(explainsSubresource('disabled', 403)[0], true);
  assert.equal(explainsSubresource('disabled', 200)[0], true);
  const [explained, why] = explainsSubresource('active', 404);
  assert.equal(explained, false);
  assert.match(why, /not explained by the repository state/);
});

test('archiving does not explain a failed read', () => {
  const [explained, why] = explainsSubresource('archived', 404);
  assert.equal(explained, false);
  assert.match(why, /leaves reads working/);
});

test('an empty repository is never reported as a ghost', () => {
  const [state, detail] = probeVerdict('active', NEW_REPO);
  assert.equal(state, 'empty-repository');
  assert.match(detail, /never been pushed to/);
  assert.equal(explainsSubresource('disabled', EMPTY_REPOSITORY)[0], false);
  assert.ok(repair(state).startsWith('nothing.'));
});

test('the ghost is the repository object reading and nothing else doing', () => {
  const [state, detail] = probeVerdict('disabled', GHOST_PROBES);
  assert.equal(state, 'ghost-confirmed');
  assert.match(detail, /3 of 4 sub-resource\(s\)/);
  assert.match(repair(state), /billing or account matter/);
});

test('a disabled repository that answers is still disabled', () => {
  const [state, detail] = probeVerdict('disabled', ALL_FINE);
  assert.equal(state, 'disabled-but-answering');
  assert.match(detail, /Trust the boolean/);
});

test('failures without a state to explain them go to the other note', () => {
  const [state, detail] = probeVerdict('active', [{ path: '/branches', status: 404 }]);
  assert.equal(state, 'not-explained-by-state');
  assert.match(detail, /neither disabled nor archived/);
  assert.match(repair(state), /credential problem/);
});

test('archived and unreadable are handed on rather than absorbed', () => {
  assert.equal(probeVerdict('archived', ALL_FINE)[0], 'archived-not-disabled');
  assert.match(repair('archived-not-disabled'), /repo-archived-writes-403/);
  assert.equal(probeVerdict('unknown', [])[0], 'repository-unreadable');
  assert.equal(probeVerdict('active', ALL_FINE)[0], 'healthy');
});

test('the aggregate decision is the output that matters', () => {
  const [decision, reason] = aggregateSafety('disabled');
  assert.equal(decision, 'exclude');
  assert.match(reason, /artefact/);
  assert.equal(aggregateSafety('archived')[0], 'include');
  assert.equal(aggregateSafety('active')[0], 'include');
  assert.equal(aggregateSafety('unknown')[0], 'exclude');
});

test('a zero from a disabled repository is not a zero', () => {
  assert.equal(isRealZero('disabled', 0), false);
  assert.equal(isRealZero('unknown', 0), false);
  assert.equal(isRealZero('active', 0), true);
  assert.equal(isRealZero('archived', 0), true);
  assert.equal(isRealZero('disabled', 4), null);
  assert.equal(isRealZero('active', null), null);
});

test('the sweep reports what it left out', () => {
  const impact = aggregateImpact([{ state: 'disabled' }, { state: 'active' },
    { state: 'archived' }, { state: 'unknown' }]);
  assert.deepEqual(impact, { counted: 2, excluded: 2, false_zeroes_avoided: 1 });
  assert.deepEqual(aggregateImpact([]),
    { counted: 0, excluded: 0, false_zeroes_avoided: 0 });
});

test('the remedy is addressed to whoever can apply it', () => {
  assert.match(remedyOwner('disabled'), /GitHub/);
  assert.match(remedyOwner('disabled'), /does not say which reason/);
  assert.match(remedyOwner('archived'), /unarchiving/);
  assert.equal(remedyOwner('active'), 'no remedy needed.');
});

test('the cost is worked out before anything is fetched', () => {
  assert.equal(DEFAULT_PROBES.length, 4);
  assert.equal(readCost(['a', 'b']), 10);
  assert.equal(readCost(['a'], ['/languages']), 2);
  assert.equal(readCost([]), 0);
  assert.equal(readCost(null), 0);
});
