import { test } from 'node:test';
import assert from 'node:assert/strict';
import { windowBurn, sampleBurn, verdict } from './github-quota-forecast.mjs';

const NOW = 1800000000;

/** A window that opened `minute` minutes ago with `used` spent. */
const atMinute = (minute, used, limit = 5000) =>
  windowBurn(used, limit, NOW + (3600 - minute * 60), NOW);

test('used alone says nothing until it is a rate', () => {
  const early = atMinute(5, 2400);
  const late = atMinute(50, 2400);
  assert.ok(early.per_min > late.per_min * 5);
  assert.equal(early.remaining, 2600);
  assert.equal(late.remaining, 2600);
});

test('a steady drain that fits leaves the window intact', () => {
  const win = atMinute(30, 1500);
  assert.equal(win.per_min, 50);
  assert.equal(win.affordable, Math.round((3500 / 30) * 100) / 100);
  assert.equal(win.empty_in, null);
});

test('a drain that does not fit names the minute', () => {
  const win = atMinute(30, 4000);
  assert.ok(win.per_min > win.affordable);
  assert.ok(win.empty_in > 400 && win.empty_in < 500);
});

test('an empty bucket empties in zero seconds', () => {
  const win = atMinute(45, 5000);
  assert.equal(win.remaining, 0);
  assert.equal(win.empty_in, 0);
});

test('the first minute does not divide by zero', () => {
  const win = windowBurn(3, 5000, NOW + 3600, NOW);
  assert.equal(win.elapsed, 1);
  assert.equal(win.per_min, 180);
});

test('a reset beyond the window is clamped rather than trusted', () => {
  const win = windowBurn(100, 5000, NOW + 9000, NOW);
  assert.equal(win.left, 3600);
  assert.equal(win.elapsed, 1);
});

test('unusable numbers return nothing rather than a guess', () => {
  assert.equal(windowBurn(null, 5000, NOW, NOW), null);
  assert.equal(windowBurn('many', 5000, NOW, NOW), null);
});

test('two samples measure the drain right now', () => {
  const first = { used: 1000, reset: NOW + 1800, at: NOW };
  const second = { used: 1030, reset: NOW + 1800, at: NOW + 30 };
  assert.deepEqual(sampleBurn(first, second), ['measured', 60]);
});

test('a rolled window is a refill, not a negative drain', () => {
  const first = { used: 4900, reset: NOW + 10, at: NOW };
  const second = { used: 12, reset: NOW + 3610, at: NOW + 30 };
  assert.deepEqual(sampleBurn(first, second), ['rolled', null]);
});

test('one sample is reported as one sample', () => {
  assert.deepEqual(sampleBurn({ used: 1, reset: NOW, at: NOW }, null), ['single', null]);
  assert.equal(sampleBurn(null, null)[0], 'single');
});

test('two samples at the same instant measure nothing', () => {
  const s = { used: 10, reset: NOW + 60, at: NOW };
  assert.deepEqual(sampleBurn(s, { ...s, used: 20 }), ['no-gap', null]);
});

test('exhausted reports the wait and refuses to call it a fix', () => {
  const [state, detail] = verdict(atMinute(45, 5000));
  assert.equal(state, 'exhausted');
  assert.match(detail, /900 second\(s\)/);
  assert.match(detail, /Waiting is not the repair/);
});

test('a measured spike overrides a comfortable average', () => {
  const [state, detail] = verdict(atMinute(50, 1000), ['measured', 600]);
  assert.equal(state, 'will-exhaust');
  assert.match(detail, /measured over the sample gap/);
});

test('a burst that still fits is flagged as spiky, not safe', () => {
  assert.equal(verdict(atMinute(10, 200), ['measured', 60])[0], 'spiky');
});

test('eighty percent used is tight even when the drain fits', () => {
  const [state, detail] = verdict(atMinute(55, 4100), ['measured', 1]);
  assert.equal(state, 'tight');
  assert.match(detail, /second consumer/);
});

test('a healthy window is clear', () => {
  const [state, detail] = verdict(atMinute(30, 900), ['measured', 30]);
  assert.equal(state, 'clear');
  assert.match(detail, /4100 left/);
});

test('an unreadable body is not reported as healthy', () => {
  assert.equal(verdict(null)[0], 'unreadable');
});
