import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  behind, classify, confirmsVersionRefusal, isVersion, nearest, supported,
} from './github-api-version-pin.mjs';

const SERVED = ['2022-11-28', '2024-06-10', '2025-04-01'];

test('a version is a real date and not just a date shape', () => {
  assert.ok(isVersion('2022-11-28'));
  assert.ok(!isVersion('2022-11-38'));
  assert.ok(!isVersion('2022-13-01'));
  assert.ok(!isVersion('latest'));
  assert.ok(!isVersion(null));
});

test('the versions body is sorted and junk is dropped', () => {
  assert.deepEqual(supported(['2024-06-10', '2022-11-28', 'latest', '']),
    ['2022-11-28', '2024-06-10']);
  assert.deepEqual(supported(null), []);
  assert.deepEqual(supported({ versions: [] }), []);
});

test('being behind is counted as the notes still to read', () => {
  assert.deepEqual(behind('2022-11-28', SERVED), ['2024-06-10', '2025-04-01']);
  assert.deepEqual(behind('2025-04-01', SERVED), []);
});

test('the nearest served version is offered for a typo', () => {
  assert.equal(nearest('2024-06-01', SERVED), '2024-06-10');
  assert.equal(nearest('2022-11-38', SERVED), '2022-11-28');
  assert.equal(nearest('2022-11-28', []), null);
});

test('the current pin is the quiet state', () => {
  const [state, detail] = classify('2025-04-01', SERVED);
  assert.equal(state, 'supported-current');
  assert.match(detail, /newest version/);
});

test('a supported but behind pin is the one to alert on', () => {
  const [state, detail] = classify('2022-11-28', SERVED);
  assert.equal(state, 'supported-behind');
  assert.match(detail, /2 newer version\(s\)/);
  assert.match(detail, /notice attached/);
});

test('a retired pin is named as older than everything served', () => {
  const [state, detail] = classify('2021-04-01', SERVED);
  assert.equal(state, 'retired');
  assert.match(detail, /2022-11-28/);
});

test('a date that was never a version is its own state', () => {
  const [state, detail] = classify('2024-06-11', SERVED);
  assert.equal(state, 'unknown-version');
  assert.match(detail, /2024-06-10/);
});

test('a future date is a typo rather than a retirement', () => {
  assert.equal(classify('2099-01-01', SERVED)[0], 'not-yet-supported');
});

test('a value that is not a date is a typo and says so', () => {
  const [state, detail] = classify('2022-11-38', SERVED);
  assert.equal(state, 'malformed-pin');
  assert.match(detail, /never valid/);
});

test('sending no header is a state rather than a pass', () => {
  const [state, detail] = classify(null, SERVED);
  assert.equal(state, 'unpinned');
  assert.match(detail, /pinned by the server/);
  assert.equal(classify('', SERVED)[0], 'unpinned');
});

test('an unpinned client is warned when the known default is gone', () => {
  const [, detail] = classify(null, ['2025-04-01']);
  assert.match(detail, /not on the served list/);
});

test('an empty versions list is a failure of the check not a finding', () => {
  const [state, detail] = classify('2022-11-28', []);
  assert.equal(state, 'no-versions-list');
  assert.match(detail, /failure of the check/);
});

test('a refusal is matched on words and not on a status code', () => {
  assert.ok(confirmsVersionRefusal(410, 'The API version is no longer supported'));
  assert.ok(confirmsVersionRefusal(400, 'X-GitHub-Api-Version is not supported'));
  assert.ok(!confirmsVersionRefusal(200, 'The API version is no longer supported'));
  assert.ok(!confirmsVersionRefusal(403, 'Resource not accessible by integration'));
  assert.ok(!confirmsVersionRefusal(null, null));
});
