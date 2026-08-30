import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  bucket, headerValue, parseExpiry, reading, schedule, secondsLeft, verdict,
} from './github-token-expiry-watch.mjs';

const NOON = 1790769600; // 2026-09-30 12:00:00 UTC

test('the documented shape parses to the right instant', () => {
  assert.equal(parseExpiry('2026-09-30 12:00:00 UTC'), NOON);
});

test('the iso shapes parse to the same instant', () => {
  assert.equal(parseExpiry('2026-09-30T12:00:00Z'), NOON);
  assert.equal(parseExpiry('2026-09-30T12:00:00+00:00'), NOON);
});

test('a numeric offset is honoured rather than ignored', () => {
  assert.equal(parseExpiry('2026-09-30 07:00:00 -0500'), NOON);
  assert.equal(parseExpiry('2026-09-30 14:00:00 +02:00'), NOON);
});

test('a bare date is read as midnight utc', () => {
  assert.equal(parseExpiry('2026-09-30'), NOON - 12 * 3600);
});

test('a shape that is not recognised returns nothing at all', () => {
  assert.equal(parseExpiry('soon'), null);
  assert.equal(parseExpiry('30/09/2026'), null);
  assert.equal(parseExpiry(''), null);
  assert.equal(parseExpiry(null), null);
});

test('the header is found whatever its case', () => {
  assert.equal(headerValue({ 'Github-Authentication-Token-Expiration': 'x' }), 'x');
  assert.equal(headerValue({ 'github-authentication-token-expiration': 'y' }), 'y');
  assert.equal(headerValue({ etag: 'z' }), null);
  assert.equal(headerValue(null), null);
});

test('remaining time is a number or nothing', () => {
  assert.equal(secondsLeft(NOON, NOON - 60), 60);
  assert.equal(secondsLeft(null, NOON), null);
  assert.equal(secondsLeft(NOON, 'later'), null);
});

test('the thresholds bucket as advertised', () => {
  assert.equal(bucket(null), 'unknown');
  assert.equal(bucket(0), 'expired');
  assert.equal(bucket(-1), 'expired');
  assert.equal(bucket(3600), 'short-lived');
  assert.equal(bucket(2 * 86400), 'critical');
  assert.equal(bucket(10 * 86400), 'warning');
  assert.equal(bucket(20 * 86400), 'notice');
  assert.equal(bucket(90 * 86400), 'ok');
});

test('custom thresholds are respected', () => {
  assert.equal(bucket(20 * 86400, [60, 30, 21]), 'critical');
});

test('a successful request with no header is its own state', () => {
  const row = reading('GITHUB_TOKEN', 200, { etag: 'abc' }, NOON);
  assert.equal(row.state, 'no-expiry-reported');
  assert.match(row.why, /never expires or its class does not report one/);
});

test('a failed request is not the same silence', () => {
  assert.equal(reading('GITHUB_TOKEN', 500, {}, NOON).state, 'unreadable');
  assert.equal(reading('GITHUB_TOKEN', 0, {}, NOON).state, 'unreadable');
});

test('a refused credential has no forecast left', () => {
  assert.equal(reading('GITHUB_TOKEN', 401, {}, NOON).state, 'rejected');
});

test('an unparseable header is reported rather than guessed', () => {
  const row = reading('GITHUB_TOKEN', 200,
    { 'github-authentication-token-expiration': 'next tuesday' }, NOON);
  assert.equal(row.state, 'unreadable-header');
  assert.match(row.why, /did not parse/);
});

test('a live reading carries the remaining seconds', () => {
  const headers = { 'github-authentication-token-expiration': '2026-09-30 12:00:00 UTC' };
  const row = reading('GITHUB_TOKEN', 200, headers, NOON - 2 * 86400);
  assert.equal(row.state, 'critical');
  assert.equal(row.seconds_left, 2 * 86400);
  assert.equal(row.expires_at, NOON);
});

test('an unreadable credential outranks a healthy one', () => {
  const rows = [
    { name: 'b', state: 'ok', seconds_left: 90 * 86400 },
    { name: 'a', state: 'unreadable', seconds_left: null },
    { name: 'c', state: 'critical', seconds_left: 2 * 86400 },
  ];
  assert.deepEqual(schedule(rows).map((row) => row.name), ['c', 'a', 'b']);
});

test('the soonest wins inside one state', () => {
  const rows = [
    { name: 'later', state: 'warning', seconds_left: 12 * 86400 },
    { name: 'sooner', state: 'warning', seconds_left: 8 * 86400 },
  ];
  assert.equal(schedule(rows)[0].name, 'sooner');
});

test('the verdict is the top row', () => {
  const ordered = schedule([{ name: 'GITHUB_CI_TOKEN', state: 'critical', seconds_left: 2 * 86400 }]);
  const [state, detail] = verdict(ordered);
  assert.equal(state, 'critical');
  assert.match(detail, /2\.0 day\(s\)/);
  assert.match(detail, /30, 14 and 3 days/);
});

test('an hour left is reported as a non event', () => {
  const [state, detail] = verdict([{ name: 'GITHUB_TOKEN', state: 'short-lived', seconds_left: 3540 }]);
  assert.equal(state, 'short-lived');
  assert.match(detail, /59 minute\(s\)/);
  assert.match(detail, /does not distinguish them/);
});

test('no expiry is a finding and not a clean bill of health', () => {
  const [state, detail] = verdict([{ name: 'GITHUB_BOT_TOKEN', state: 'no-expiry-reported', seconds_left: null }]);
  assert.equal(state, 'no-expiry-reported');
  assert.match(detail, /larger standing risk/);
});

test('nothing named is reported as nothing checked', () => {
  assert.equal(verdict([])[0], 'nothing-checked');
});
