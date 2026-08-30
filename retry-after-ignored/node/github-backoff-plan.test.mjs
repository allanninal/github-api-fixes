import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  backoff, plan, requiredWait, retryAfterSeconds, wastedRequests,
} from './github-backoff-plan.mjs';

const NOW = 1756512000; // 2025-08-30T00:00:00Z

test('retry-after reads the integer form', () => {
  assert.equal(retryAfterSeconds('120', NOW), 120);
  assert.equal(retryAfterSeconds(' 60 ', NOW), 60);
});

test('retry-after reads the HTTP-date form a proxy may substitute', () => {
  assert.equal(retryAfterSeconds('Sat, 30 Aug 2025 00:02:00 GMT', NOW), 120);
});

test('a retry-after already in the past is zero, not negative', () => {
  assert.equal(retryAfterSeconds('Fri, 29 Aug 2025 23:00:00 GMT', NOW), 0);
});

test('an unparseable retry-after is absent rather than zero', () => {
  assert.equal(retryAfterSeconds('soon', NOW), null);
  assert.equal(retryAfterSeconds(null, NOW), null);
  assert.equal(retryAfterSeconds('', NOW), null);
});

test('retry-after wins over the reset timestamp', () => {
  const [seconds, source] = requiredWait(403, {
    'Retry-After': '120',
    'X-RateLimit-Remaining': '4870',
    'X-RateLimit-Reset': String(NOW + 3000),
  }, NOW);
  assert.equal(source, 'retry-after');
  assert.equal(seconds, 120);
});

test('an empty bucket falls through to the reset timestamp', () => {
  const [seconds, source, detail] = requiredWait(403, {
    'x-ratelimit-remaining': '0',
    'x-ratelimit-reset': String(NOW + 1800),
  }, NOW);
  assert.equal(source, 'x-ratelimit-reset');
  assert.equal(seconds, 1800);
  assert.match(detail, /hourly quota/);
});

test('a bucket with headroom and no retry-after uses the floor', () => {
  const [seconds, source] = requiredWait(429, { 'x-ratelimit-remaining': '4900' }, NOW);
  assert.equal(source, 'floor');
  assert.equal(seconds, 60);
});

test('a response that is not throttled asks for no wait', () => {
  const [seconds, source] = requiredWait(200, { 'retry-after': '120' }, NOW);
  assert.equal(source, 'none');
  assert.equal(seconds, 0);
});

test('backoff doubles and then stops at the cap', () => {
  assert.deepEqual([0, 1, 2, 3, 4].map((i) => backoff(i)), [1, 2, 4, 8, 16]);
  assert.equal(backoff(20), 60);
  assert.equal(backoff(-3), 1);
});

test('wastedRequests counts what fits inside the wait', () => {
  assert.equal(wastedRequests(120, 1), 120);
  assert.equal(wastedRequests(120, 30), 4);
  assert.equal(wastedRequests(0, 1), 0);
});

test('wastedRequests survives a nonsense interval', () => {
  assert.equal(wastedRequests(120, 0), 0);
  assert.equal(wastedRequests(120, -5), 0);
});

test('a one-second retry inside a two-minute wait is hammering', () => {
  const [state, report] = plan(403, { 'retry-after': '120' }, NOW, 1);
  assert.equal(state, 'hammering');
  assert.equal(report.wasted_requests, 120);
  assert.equal(report.source, 'retry-after');
});

test('a client that waits longer than asked has honoured it', () => {
  const [state, report] = plan(403, { 'retry-after': '120' }, NOW, 300);
  assert.equal(state, 'honoured');
  assert.equal(report.wasted_requests, 0);
});

test('a few retries inside the window are impatient, not hammering', () => {
  assert.equal(plan(429, { 'retry-after': '120' }, NOW, 30)[0], 'impatient');
});

test('an untroubled response reports nothing to do', () => {
  const [state, report] = plan(200, {}, NOW, 1);
  assert.equal(state, 'not-throttled');
  assert.equal(report.wait_seconds, 0);
});
