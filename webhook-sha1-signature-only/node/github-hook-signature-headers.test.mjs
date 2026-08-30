import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  formatHit, headerNames, normalized, receiverState, redactedConfig,
  repair, scanLine, scanSource, secretState, signatureHeaders, verdict,
} from './github-hook-signature-headers.mjs';

const MODERN_LINE = 'const sig = req.headers["X-Hub-Signature-256"];';
const LEGACY_LINE = 'const sig = req.headers["X-Hub-Signature"];';
const WSGI_LINE = 'sig = environ["HTTP_X_HUB_SIGNATURE_256"]';

const SECRET_SET = {
  id: 1,
  config: { url: 'https://example.com/hook', secret: '********', content_type: 'json' },
};
const NO_SECRET = {
  id: 2,
  config: { url: 'https://example.com/hook', content_type: 'json' },
};

test('the modern header is not read as a legacy one', () => {
  assert.deepEqual(scanLine(MODERN_LINE), ['sha256']);
  assert.deepEqual(scanLine(LEGACY_LINE), ['sha1']);
  assert.deepEqual(scanLine('nothing to see here'), []);
});

test('a line naming both headers reports both', () => {
  const line = 'const h = req.headers["x-hub-signature-256"] ?? req.headers["x-hub-signature"];';
  assert.deepEqual(scanLine(line), ['sha256', 'sha1']);
});

test('the runtime spellings are the same header', () => {
  assert.deepEqual(scanLine(WSGI_LINE), ['sha256']);
  assert.deepEqual(scanLine('X_HUB_SIGNATURE'), ['sha1']);
  assert.equal(normalized('X-Hub_Signature-256'), 'x-hub-signature-256');
});

test('the scan reports line numbers and never lines', () => {
  const text = ['import os', LEGACY_LINE, '', MODERN_LINE].join('\n');
  const hits = scanSource(text, 'receiver/hooks.js');
  assert.deepEqual(hits, [
    ['receiver/hooks.js', 2, 'sha1'],
    ['receiver/hooks.js', 4, 'sha256'],
  ]);
  const rendered = hits.map(formatHit);
  assert.equal(rendered[0], 'receiver/hooks.js:2 legacy X-Hub-Signature');
  assert.equal(rendered[1], 'receiver/hooks.js:4 modern X-Hub-Signature-256');
  assert.ok(!rendered.some((line) => line.includes('req.headers')));
});

test('the receiver state separates only legacy from both', () => {
  assert.equal(receiverState([['a', 1, 'sha1']]), 'sha1-only');
  assert.equal(receiverState([['a', 1, 'sha256']]), 'sha256-only');
  assert.equal(receiverState([['a', 1, 'sha256'], ['a', 1, 'sha1']]), 'both');
  assert.equal(receiverState([]), 'none');
});

test('a masked secret is presence and never a value', () => {
  assert.equal(secretState(SECRET_SET), 'set');
  assert.equal(secretState(NO_SECRET), 'absent');
  assert.equal(secretState({ id: 3 }), 'unknown');
  const safe = redactedConfig(SECRET_SET.config);
  assert.equal(safe.secret, '<set>');
  assert.ok(!JSON.stringify(safe).includes('********'));
});

test('header names are matched exactly and values dropped', () => {
  const sent = {
    'X-Hub-Signature': 'sha1=deadbeef',
    'X-Hub-Signature-256': 'sha256=deadbeef',
    'Content-Type': 'application/json',
  };
  assert.deepEqual(signatureHeaders(sent), { sha256: true, sha1: true });
  assert.deepEqual(signatureHeaders({ 'X-Hub-Signature-256': 'x' }),
    { sha256: true, sha1: false });
  assert.deepEqual(signatureHeaders({}), { sha256: false, sha1: false });
  assert.ok(!JSON.stringify(headerNames(sent)).includes('deadbeef'));
});

test('delivery headers arrive in more than one shape', () => {
  const asList = [
    { name: 'X-Hub-Signature-256', value: 'sha256=x' },
    { name: 'Content-Type', value: 'application/json' },
  ];
  assert.equal(signatureHeaders(asList).sha256, true);
  assert.equal(signatureHeaders(['X-Hub-Signature: sha1=x']).sha1, true);
  assert.deepEqual(signatureHeaders(null), { sha256: false, sha1: false });
});

test('a legacy receiver is the finding', () => {
  const [state, detail] = verdict('set', { sha256: true, sha1: true }, 'sha1-only');
  assert.equal(state, 'sha1-only');
  assert.match(detail, /being ignored/);
  assert.match(repair(state), /constant-time/);
});

test('accepting both headers is still a finding', () => {
  const [state, detail] = verdict('set', { sha256: true, sha1: true }, 'both');
  assert.equal(state, 'both-accepted');
  assert.match(detail, /weaker/);
});

test('a hook with no secret is sent to a different note', () => {
  const [state, detail] = verdict('absent', null, 'sha1-only');
  assert.equal(state, 'no-secret');
  assert.match(detail, /different and larger problem/);
});

test('with no source the script declines to guess', () => {
  const [state, detail] = verdict('set', { sha256: true, sha1: true }, null);
  assert.equal(state, 'not-scanned');
  assert.match(detail, /not visible from the API/);
});

test('finding nothing is not reported as finding a problem', () => {
  const [state, detail] = verdict('set', { sha256: true, sha1: true }, 'none');
  assert.equal(state, 'no-verification-found');
  assert.match(detail, /at runtime/);
});

test('a correct receiver passes', () => {
  const [state] = verdict('set', { sha256: true, sha1: true }, 'sha256-only');
  assert.equal(state, 'sha256-only');
  assert.ok(repair(state).startsWith('nothing'));
});
