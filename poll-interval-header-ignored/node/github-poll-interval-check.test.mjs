import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  assess, floorSeconds, parseMaxAge, verdict,
} from './github-poll-interval-check.mjs';

test('the declared interval wins and is named as the source', () => {
  const [seconds, source] = floorSeconds({ 'X-Poll-Interval': '60' });
  assert.equal(seconds, 60);
  assert.equal(source, 'x-poll-interval');
});

test('header case does not matter', () => {
  assert.equal(floorSeconds({ 'x-poll-interval': '90' })[0], 90);
});

test('cache-control is the fallback before the assumption', () => {
  const [seconds, source] = floorSeconds({ 'Cache-Control': 'public, max-age=45, s-maxage=60' });
  assert.equal(seconds, 45);
  assert.equal(source, 'cache-control max-age');
});

test('a missing header is labelled as an assumption', () => {
  const [seconds, source] = floorSeconds({});
  assert.equal(seconds, 60);
  assert.equal(source, 'documented default');
});

test('junk and zero values do not become the floor', () => {
  assert.equal(floorSeconds({ 'x-poll-interval': 'soon' })[1], 'documented default');
  assert.equal(floorSeconds({ 'x-poll-interval': '0' })[1], 'documented default');
  assert.equal(parseMaxAge('max-age=0'), null);
  assert.equal(parseMaxAge(null), null);
});

test('polling under the floor counts the requests that cannot help', () => {
  const result = assess(5, 60, false);
  assert.equal(result.state, 'under-floor');
  assert.equal(result.polls_per_hour, 720);
  assert.equal(result.allowed_per_hour, 60);
  assert.equal(result.wasted_per_hour, 660);
  assert.equal(result.billable_per_hour, 660);
});

test('an etag makes the same extra polls free', () => {
  const result = assess(5, 60, true);
  assert.equal(result.wasted_per_hour, 660);
  assert.equal(result.billable_per_hour, 0);
});

test('the floor itself is at the floor', () => {
  assert.equal(assess(60, 60, true).state, 'at-floor');
  assert.equal(assess(75, 60, true).state, 'at-floor');
});

test('polling far slower is measured in staleness, not requests', () => {
  const result = assess(600, 60, true);
  assert.equal(result.state, 'over-floor');
  assert.equal(result.wasted_per_hour, 0);
  assert.equal(result.extra_staleness_s, 540);
});

test('a zero interval is clamped rather than dividing by zero', () => {
  assert.equal(assess(0, 60, true).polls_per_hour, 3600);
});

test('extra polls without an etag are a quota finding', () => {
  const [state, detail] = verdict(assess(5, 60, false));
  assert.equal(state, 'burning-quota');
  assert.match(detail, /660 request\(s\)/);
});

test('extra polls with an etag are pointless rather than expensive', () => {
  const [state, detail] = verdict(assess(5, 60, true));
  assert.equal(state, 'free-but-pointless');
  assert.match(detail, /cost no quota/);
});

test('too slow is reported as staleness', () => {
  const [state, detail] = verdict(assess(600, 60, true));
  assert.equal(state, 'slower-than-needed');
  assert.match(detail, /540s/);
});

test('matching the floor has nothing to reclaim', () => {
  const [state, detail] = verdict(assess(60, 60, true));
  assert.equal(state, 'at-floor');
  assert.match(detail, /either direction/);
});
