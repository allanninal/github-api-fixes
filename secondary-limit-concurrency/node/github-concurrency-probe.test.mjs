import { test } from 'node:test';
import assert from 'node:assert/strict';
import { classify, peakOverlap, verdict } from './github-concurrency-probe.mjs';

const SECONDARY = '{"message":"You have exceeded a secondary rate limit. ' +
  'Please wait a few minutes before you try again."}';
const PRIMARY = '{"message":"API rate limit exceeded for user ID 12345."}';
const DENIED = '{"message":"Resource not accessible by integration"}';

const headers = (remaining = 4800, extra = {}) => ({
  'X-RateLimit-Limit': '5000',
  'X-RateLimit-Used': String(5000 - remaining),
  'X-RateLimit-Remaining': String(remaining),
  ...extra,
});

test('a secondary limit is named in the body', () => {
  const [state, detail] = classify(403, SECONDARY, headers(4800));
  assert.equal(state, 'secondary');
  assert.match(detail, /4800/);
});

test('the same message on a 429 classifies identically', () => {
  assert.equal(classify(429, SECONDARY, headers(4800))[0], 'secondary');
});

test('an empty bucket is the primary quota, not a secondary limit', () => {
  const [state, detail] = classify(403, PRIMARY, headers(0));
  assert.equal(state, 'primary');
  assert.match(detail, /x-ratelimit-reset/);
});

test('headroom left is enough to suspect a secondary limit', () => {
  const [state, detail] = classify(403, '{"message":"Something new"}', headers(4321));
  assert.equal(state, 'secondary-suspected');
  assert.match(detail, /4321/);
});

test('a 403 with no rate-limit headers is a permissions problem', () => {
  assert.equal(classify(403, DENIED, {})[0], 'forbidden');
});

test('header case does not change the verdict', () => {
  assert.equal(classify(403, PRIMARY, { 'x-ratelimit-remaining': '0' })[0], 'primary');
});

test('a 404 is not a throttle', () => {
  assert.equal(classify(404, '{"message":"Not Found"}', headers())[0], 'other');
});

test('a success is reported with its headroom', () => {
  const [state, detail] = classify(200, '{}', headers(4999));
  assert.equal(state, 'ok');
  assert.match(detail, /4999/);
});

test('overlap of sequential requests is one', () => {
  assert.equal(peakOverlap([[0, 1], [1, 2], [2, 3]]), 1);
});

test('overlap counts only spans open at the same instant', () => {
  assert.equal(peakOverlap([[0, 3], [1, 2], [1.5, 4]]), 3);
  assert.equal(peakOverlap([[0, 1], [0.5, 2]]), 2);
});

test('an empty probe has no overlap', () => {
  assert.equal(peakOverlap([]), 0);
  assert.equal(peakOverlap(null), 0);
});

test('a reversed span is still measured', () => {
  assert.equal(peakOverlap([[2, 0], [1, 1.5]]), 2);
});

test('any throttled response beats a low peak', () => {
  const [state, detail] = verdict(3, ['ok', 'secondary', 'ok']);
  assert.equal(state, 'tripped');
  assert.match(detail, /1 of 3/);
});

test('a peak at the ceiling is reported even when nothing failed', () => {
  assert.equal(verdict(100, new Array(100).fill('ok'))[0], 'over-ceiling');
  assert.equal(verdict(85, ['ok'])[0], 'near-ceiling');
});

test('clear does not claim the client is safe', () => {
  const [state, detail] = verdict(6, ['ok', 'ok']);
  assert.equal(state, 'clear');
  assert.match(detail, /headroom API/);
});
