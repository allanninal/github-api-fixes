import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  classify, endpoint, hasSecret, insecureFlag, repair, schemeOf,
  summarize, unchangedDays,
} from './github-hook-ssl-verification.mjs';

const NOW = Date.parse('2026-08-30T12:00:00Z');

const OPEN = {
  id: 1, updated_at: '2025-09-23T08:00:00Z',
  config: {
    url: 'https://hooks.acme.io/github', insecure_ssl: '1',
    secret: '********', content_type: 'json',
  },
};
const SAFE = {
  id: 2, updated_at: '2026-08-01T08:00:00Z',
  config: {
    url: 'https://hooks.acme.io/github', insecure_ssl: '0',
    secret: '********', content_type: 'json',
  },
};
const PLAIN = {
  id: 3, updated_at: '2026-08-01T08:00:00Z',
  config: { url: 'http://hooks.acme.io/github', insecure_ssl: '0' },
};

test('the string zero is not a finding', () => {
  assert.equal(insecureFlag(SAFE), 'off');
  assert.equal(insecureFlag({ config: { insecure_ssl: 0 } }), 'off');
  assert.equal(insecureFlag({ config: { insecure_ssl: false } }), 'off');
  assert.equal(classify(SAFE, NOW)[0], 'verified');
});

test('every spelling of on is on', () => {
  assert.equal(insecureFlag(OPEN), 'on');
  assert.equal(insecureFlag({ config: { insecure_ssl: 1 } }), 'on');
  assert.equal(insecureFlag({ config: { insecure_ssl: true } }), 'on');
  assert.equal(insecureFlag({ config: { insecure_ssl: 'true' } }), 'on');
});

test('an absent flag is unknown rather than either answer', () => {
  assert.equal(insecureFlag({ config: { url: 'https://x.example' } }), 'unknown');
  assert.equal(insecureFlag({ config: { insecure_ssl: 'maybe' } }), 'unknown');
  assert.equal(insecureFlag({ id: 4 }), 'unknown');
  const [state, detail] = classify({ id: 4, config: { url: 'https://x.example' } }, NOW);
  assert.equal(state, 'flag-unreadable');
  assert.match(detail, /rather than assuming/);
});

test('a plaintext hook is handed to the other question', () => {
  const [state, detail] = classify(PLAIN, NOW);
  assert.equal(state, 'not-applicable');
  assert.match(detail, /the scheme is/);
  assert.match(repair(state, PLAIN), /behind HTTPS/);
});

test('the finding names the endpoint and dates it as a lower bound', () => {
  const [state, detail] = classify(OPEN, NOW);
  assert.equal(state, 'verification-off');
  assert.match(detail, /https:\/\/hooks\.acme\.io\/github/);
  assert.match(detail, /at least 341 day\(s\)/);
  assert.equal(unchangedDays(OPEN, NOW), 341);
});

test('a hook with no url is its own state', () => {
  const [state] = classify({ id: 5, config: { insecure_ssl: '1' } }, NOW);
  assert.equal(state, 'no-url');
});

test('the printed url drops any query string', () => {
  const hook = { id: 6, config: { url: 'https://hooks.acme.io/github?token=abc123' } };
  assert.equal(endpoint(hook), 'https://hooks.acme.io/github');
  assert.equal(schemeOf(hook), 'https');
  assert.equal(schemeOf({ config: { url: 'not-a-url' } }), '');
});

test('the repair is a whole config not one field', () => {
  const text = repair('verification-off', OPEN);
  assert.match(text, /full/);
  assert.match(text, /replaced, not merged/);
  assert.match(text, /new secret/);
});

test('a hook with no secret gets told to set one', () => {
  const hookless = { id: 7, config: { url: 'https://x.example', insecure_ssl: '1' } };
  assert.ok(!hasSecret(hookless));
  assert.match(repair('verification-off', hookless), /since this hook has none/);
});

test('the summary keeps the plaintext hooks out of the finding count', () => {
  assert.deepEqual(summarize([OPEN, SAFE, PLAIN], NOW), {
    total: 3, verification_off: 1, verified: 1, plaintext: 1, unreadable: 0,
  });
});

test('an unparseable timestamp produces no age', () => {
  assert.equal(unchangedDays({ updated_at: 'whenever' }, NOW), null);
  assert.equal(unchangedDays({ id: 1 }, NOW), null);
});
