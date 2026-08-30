import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  GRACE, backdateNeeded, bestSample, classify, classifyRate, driftRate,
  interpret, parseHttpDate, sampleSkew, timezoneSuspect,
} from './github-clock-skew.mjs';

const NOW = 1772000000;

test('a date header parses to epoch seconds', () => {
  assert.equal(parseHttpDate('Thu, 01 Jan 1970 00:00:10 GMT'), 10);
  assert.equal(parseHttpDate('not a date'), null);
  assert.equal(parseHttpDate(''), null);
  assert.equal(parseHttpDate(null), null);
});

test('one exchange becomes an offset with an error bar', () => {
  const s = sampleSkew(NOW, NOW + 40.0, NOW + 40.4);
  assert.equal(s.skew, 40.2);
  assert.equal(s.uncertainty, 1.2);
  assert.equal(s.round_trip, 0.4);
});

test('a response without a date produces no sample', () => {
  assert.equal(sampleSkew(null, NOW, NOW + 0.2), null);
});

test('the fastest exchange wins rather than the average', () => {
  const slow = sampleSkew(NOW, NOW + 40.0, NOW + 44.0);
  const quick = sampleSkew(NOW, NOW + 40.0, NOW + 40.1);
  assert.equal(bestSample([slow, quick, null]).round_trip, 0.1);
  assert.equal(bestSample([]), null);
  assert.equal(bestSample([null]), null);
});

test('a host running fast with no backdate is the headline finding', () => {
  const [state, detail] = classify(40.0, 1.0, 0);
  assert.equal(state, 'iat-lands-in-the-future');
  assert.match(detail, /41.0s into GitHub/);
});

test('the same offset is harmless once iat is backdated', () => {
  const [state, detail] = classify(40.0, 1.0, 60);
  assert.equal(state, 'drift-absorbed-by-backdate');
  assert.match(detail, /19.0s to spare/);
});

test('a backdate that only just covers the drift is still flagged', () => {
  assert.equal(classify(58.0, 1.0, 60)[0], 'backdate-has-no-headroom');
});

test('a slow path cannot resolve a small offset', () => {
  assert.equal(classify(3.0, 4.0, 60)[0], 'clock-in-sync');
});

test('a clock behind github is its own state and its own consequence', () => {
  const [state, detail] = classify(-45.0, 1.0, 60);
  assert.equal(state, 'clock-behind-github');
  assert.match(detail, /already spent 45.0s/);
});

test('whole hours are a timezone and not drift', () => {
  assert.equal(timezoneSuspect(-18000), -5);
  assert.equal(timezoneSuspect(19800), 5.5);
  assert.equal(timezoneSuspect(41), null);
  assert.equal(timezoneSuspect(2400), null);
  const [state, detail] = classify(18000, 1.0, 60);
  assert.equal(state, 'timezone-not-drift');
  assert.match(detail, /naive local datetime/);
});

test('the backdate recommendation covers the offset and its error bar', () => {
  assert.equal(backdateNeeded(5, 1), 60);
  assert.equal(backdateNeeded(200, 2), 210);
  assert.equal(backdateNeeded(-30, 1), 60);
});

test('a rate is refused when the samples are too close together', () => {
  assert.equal(driftRate([[NOW, 10.0], [NOW + 4, 10.4]]), null);
  const [state, detail] = classifyRate(null);
  assert.equal(state, 'rate-not-measurable');
  assert.match(detail, /60s/);
});

test('a growing offset is reported as a free running clock', () => {
  const ppm = driftRate([[NOW, 10.0], [NOW + 100, 10.05]]);
  assert.equal(ppm, 500);
  const [state, detail] = classifyRate(ppm);
  assert.equal(state, 'clock-is-running-free');
  assert.match(detail, /43.2 seconds a day/);
});

test('a static offset says the clock was set wrong once', () => {
  assert.equal(classifyRate(driftRate([[NOW, 40], [NOW + 600, 40]]))[0], 'offset-is-static');
});

test('an unmeasurable clock says so rather than guessing', () => {
  assert.equal(classify(null, 1.0, 60)[0], 'unmeasurable');
});

test('the live messages separate iat from its neighbours', () => {
  assert.equal(interpret(200, null)[0], 'accepted');
  assert.equal(interpret(401,
    "'Issued at' claim ('iat') must be an Integer representing the time that the assertion was issued")[0],
  'github-refused-iat');
  assert.equal(interpret(401, "'Expiration time' claim ('exp') is too far in the future")[0],
    'lifetime-not-drift');
  assert.equal(interpret(401, 'A JSON web token could not be decoded')[0], 'key-or-encoding');
  assert.equal(interpret(404, 'Integration not found')[0], 'issuer-does-not-resolve');
  assert.equal(interpret(403, 'Resource not accessible by integration')[0], 'unrelated');
});

test('the grace band is the one place a number is shared', () => {
  assert.equal(GRACE, 5);
});
