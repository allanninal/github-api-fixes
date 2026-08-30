import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  classify, plan, poolResetIn, verdict,
} from './github-actions-token-budget.mjs';

test('a thousand is the actions token', () => {
  const [klass, confidence, note] = classify(1000);
  assert.equal(klass, 'actions-token');
  assert.equal(confidence, 'likely');
  assert.match(note, /belongs to the repository/);
});

test('two corroborating signals raise the confidence', () => {
  const [klass, confidence, note] = classify(1000, 1000, 403);
  assert.equal(klass, 'actions-token');
  assert.equal(confidence, 'high');
  assert.match(note, /403/);
  assert.match(note, /1000 points/);
});

test('five thousand is reported as ambiguous rather than as a user', () => {
  const [klass, confidence] = classify(5000);
  assert.equal(klass, 'user-or-app');
  assert.equal(confidence, 'ambiguous');
});

test('the scaled and enterprise ceilings are separated', () => {
  assert.equal(classify(15000)[0], 'enterprise-user');
  assert.equal(classify(12500)[0], 'app-installation');
});

test('sixty is the anonymous tier and not a budget problem', () => {
  assert.equal(classify(60)[0], 'anonymous');
  assert.equal(verdict('anonymous', plan(4, 100))[0], 'unauthenticated');
});

test('an unreadable ceiling does not become a number', () => {
  assert.equal(classify(null)[0], 'unknown');
  assert.equal(classify('plenty')[1], 'none');
});

test('the matrix multiplies the job count', () => {
  const costing = plan(4, 120, 3);
  assert.equal(costing.legs, 3);
  assert.equal(costing.jobs, 12);
  assert.equal(costing.total, 1440);
});

test('an overrun names the first job that starves', () => {
  const costing = plan(12, 120, 1, 1000);
  assert.equal(costing.fits, false);
  assert.equal(costing.jobs_served, 8);
  assert.equal(costing.first_starved_job, 9);
  assert.equal(costing.shortfall, 440);
});

test('remaining is used when it is supplied and is labelled', () => {
  const costing = plan(5, 100, 1, 1000, 240);
  assert.equal(costing.source, 'remaining');
  assert.equal(costing.headroom, 240);
  assert.equal(costing.first_starved_job, 3);
});

test('no calls is not a division by zero', () => {
  const costing = plan(6, 0);
  assert.equal(costing.total, 0);
  assert.equal(costing.fits, true);
  assert.equal(costing.first_starved_job, null);
});

test('a described overrun reads as a job number', () => {
  const [state, detail] = verdict('actions-token', plan(12, 120, 1, 1000));
  assert.equal(state, 'pool-overrun');
  assert.match(detail, /Job 9 of 12/);
  assert.match(detail, /whole repository shares/);
});

test('four fifths of the pool is already a finding', () => {
  const [state, detail] = verdict('actions-token', plan(8, 100, 1, 1000));
  assert.equal(state, 'pool-tight');
  assert.match(detail, /four fifths/);
});

test('a run well inside the pool is reported as fitting', () => {
  assert.equal(verdict('actions-token', plan(2, 50, 1, 1000))[0], 'fits');
});

test('costing a laptop credential says so rather than passing', () => {
  const [state, detail] = verdict('user-or-app', plan(12, 120, 1, 5000));
  assert.equal(state, 'different-ceiling');
  assert.match(detail, /from inside the job/);
});

test('nothing described is not a pass either', () => {
  assert.equal(verdict('actions-token', plan(0, 0))[0], 'no-workflow');
});

test('the reset is seconds or nothing', () => {
  assert.equal(poolResetIn(1000, 940), 60);
  assert.equal(poolResetIn(900, 940), 0);
  assert.equal(poolResetIn(null, 940), null);
});
