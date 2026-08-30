import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  secretState, unauthorized, verdict,
} from './github-hook-secret-audit.mjs';

const SIGNED = {
  id: 1,
  config: { url: 'https://hooks.example.com/gh', secret: '********', content_type: 'json' },
};
const UNSIGNED = {
  id: 2,
  config: { url: 'https://hooks.example.com/gh', content_type: 'json' },
};

test('a missing key is the finding', () => {
  assert.equal(secretState(UNSIGNED), 'absent');
});

test('a masked value means a secret exists', () => {
  assert.equal(secretState(SIGNED), 'set');
});

test('an empty secret counts as absent', () => {
  assert.equal(secretState({ config: { secret: '  ' } }), 'absent');
});

test('a hook without config is not silently signed', () => {
  assert.equal(secretState({ id: 3 }), 'unknown');
  assert.equal(verdict({ id: 3 })[0], 'unknown');
});

test('the unsigned detail names the missing header', () => {
  const [state, detail] = verdict(UNSIGNED);
  assert.equal(state, 'unsigned');
  assert.match(detail, /X-Hub-Signature-256/);
  assert.match(detail, /hooks\.example\.com/);
});

test('signed admits it cannot check the value', () => {
  const [state, detail] = verdict(SIGNED);
  assert.equal(state, 'signed');
  assert.match(detail, /masked/);
  assert.match(detail, /whether it matches/);
});

test('a run of refusals on a signed hook is its own state', () => {
  const [state, detail] = verdict(SIGNED, 18, 20);
  assert.equal(state, 'rejected');
  assert.match(detail, /mismatched secret/);
});

test('one refusal in fifty is not a mismatch', () => {
  const [state, detail] = verdict(SIGNED, 1, 50);
  assert.equal(state, 'signed');
  assert.match(detail, /1 of 50/);
});

test('unauthorized counts only auth failures', () => {
  const { rejected, total } = unauthorized([{ status_code: 401 },
    { status_code: 403 }, { status_code: 500 }, { status_code: 200 },
    { status_code: null }]);
  assert.equal(rejected, 2);
  assert.equal(total, 5);
});
