import { test } from 'node:test';
import assert from 'node:assert/strict';
import { measure, project, verdict } from './github-etag-saving.mjs';

const ETAG = 'W/"6c1a2f9e0b7d4a3c"';

const response = (status, etag = ETAG, used = null) => ({ status, etag, used });

test('a 304 that did not move the counter is the finding', () => {
  const [state, report] = measure(response(200, ETAG, 101), response(304, ETAG, 101));
  assert.equal(state, 'free');
  assert.equal(report.cost_of_unchanged_poll, 0);
  assert.equal(report.etag, ETAG);
});

test('an endpoint with no etag cannot be polled conditionally', () => {
  const [state] = measure(response(200, null, 10), response(200, ETAG, 11));
  assert.equal(state, 'no-etag');
});

test('a 200 answer to a conditional request is its own finding', () => {
  const [state, report] = measure(response(200, ETAG, 10), response(200, ETAG, 11));
  assert.equal(state, 'not-honoured');
  assert.equal(report.cost_of_unchanged_poll, 1);
});

test('a 304 that still billed is reported rather than smoothed over', () => {
  const [state, report] = measure(response(200, ETAG, 10), response(304, ETAG, 12));
  assert.equal(state, 'billed');
  assert.equal(report.cost_of_unchanged_poll, 2);
});

test('a missing used header leaves the saving unmeasured', () => {
  const [state, report] = measure(response(200, ETAG, null), response(304, ETAG, null));
  assert.equal(state, 'unmeasured');
  assert.equal(report.cost_of_unchanged_poll, null);
});

test('the projection prices a real polling schedule', () => {
  const p = project(30, 8, 5000, 1);
  assert.equal(p.per_hour_without, 960);
  assert.equal(p.per_hour_with, 0);
  assert.equal(p.saved_per_hour, 960);
  assert.equal(p.percent_without, 19.2);
});

test('a partly changing workload saves only part of it', () => {
  const p = project(60, 1, 5000, 0.75);
  assert.equal(p.per_hour_without, 60);
  assert.equal(p.per_hour_with, 15);
  assert.equal(p.saved_per_hour, 45);
});

test('nothing unchanged means nothing saved', () => {
  assert.equal(project(60, 1, 5000, 0).saved_per_hour, 0);
});

test('the projection refuses nonsense inputs instead of dividing by zero', () => {
  const p = project(0, 0, 0, 5);
  assert.equal(p.limit, 1);
  assert.equal(p.per_hour_without, 3600);
  assert.equal(p.per_hour_with, 0);
});

test('a large share of quota is called out as such', () => {
  const [level, detail] = verdict('free', project(30, 8, 5000, 1));
  assert.equal(level, 'saving');
  assert.match(detail, /19\.2%/);
  assert.equal(verdict('free', project(10, 8, 5000, 1))[0], 'large-saving');
});

test('each unhappy state names a different repair', () => {
  assert.equal(verdict('no-etag', project(60, 1))[0], 'unavailable');
  assert.equal(verdict('not-honoured', project(60, 1))[0], 'ignored');
  assert.equal(verdict('billed', project(60, 1))[0], 'billed');
  assert.equal(verdict('unmeasured', project(60, 1))[0], 'unmeasured');
});

test('the ignored state blames the header, not the quota', () => {
  assert.match(verdict('not-honoured', project(60, 1))[1], /If-None-Match/);
});

test('the billed state points at the shared counter', () => {
  assert.match(verdict('billed', project(60, 1))[1], /shar/);
});
