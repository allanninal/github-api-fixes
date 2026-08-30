import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  byActor, parseTs, peakRate, verdict,
} from './github-content-burst-audit.mjs';

const NOW = 1756512000; // 2025-08-30T00:00:00Z, so every case below is anchored.

const issue = (login, created_at, type = 'Bot') => ({ user: { login, type }, created_at });

test('parseTs reads the Z suffix GitHub sends', () => {
  assert.equal(parseTs('2025-08-30T00:00:00Z'), NOW);
});

test('parseTs returns null rather than throwing', () => {
  assert.equal(parseTs(null), null);
  assert.equal(parseTs(''), null);
  assert.equal(parseTs('last tuesday'), null);
});

test('peakRate of a steady trickle is one per window', () => {
  const times = Array.from({ length: 10 }, (_, i) => NOW + 120 * i);
  assert.equal(peakRate(times, 60)[0], 1);
});

test('peakRate finds the burst and says when it ended', () => {
  const times = [...Array.from({ length: 90 }, (_, i) => NOW + i), NOW + 10000];
  const [peak, at] = peakRate(times, 60);
  assert.equal(peak, 60);
  assert.equal(at, NOW + 59);
});

test('the window edge is exclusive so a full minute counts once', () => {
  assert.equal(peakRate([NOW, NOW + 60], 60)[0], 1);
  assert.equal(peakRate([NOW, NOW + 59.9], 60)[0], 2);
});

test('peakRate of nothing is zero', () => {
  assert.deepEqual(peakRate([], 60), [0, null]);
  assert.deepEqual(peakRate(null, 60), [0, null]);
});

test('byActor groups per login and keeps the account type', () => {
  const grouped = byActor([
    issue('bot', '2025-08-30T00:00:00Z'),
    issue('bot', '2025-08-30T00:00:01Z'),
    issue('person', '2025-08-30T00:00:02Z', 'User'),
  ]);
  assert.deepEqual(Object.keys(grouped).sort(), ['bot', 'person']);
  assert.equal(grouped.bot.times.length, 2);
  assert.equal(grouped.person.type, 'User');
});

test('byActor drops items with no readable timestamp', () => {
  const grouped = byActor([issue('bot', null), issue('bot', '2025-08-30T00:00:00Z')]);
  assert.equal(grouped.bot.times.length, 1);
});

test('eighty in a minute is the finding', () => {
  const [state, detail] = verdict(80, 80, NOW, NOW);
  assert.equal(state, 'over-minute');
  assert.match(detail, /still running/);
});

test('a burst that finished hours ago is reported as finished', () => {
  const [state, detail] = verdict(90, 90, NOW - 7200, NOW);
  assert.equal(state, 'over-minute');
  assert.match(detail, /already finished/);
  assert.match(detail, /120 minute/);
});

test('a gentle rate can still break the hourly ceiling', () => {
  const [state, detail] = verdict(10, 600, NOW, NOW);
  assert.equal(state, 'over-hour');
  assert.match(detail, /per-minute limit is not enough/);
});

test('the near states warn before the ceiling', () => {
  assert.equal(verdict(64, 64, NOW, NOW)[0], 'near-minute');
  assert.equal(verdict(10, 400, NOW, NOW)[0], 'near-hour');
});

test('an ordinary repository is clear', () => {
  assert.equal(verdict(3, 40, NOW, NOW)[0], 'clear');
});

test('no activity is quiet rather than clear', () => {
  assert.equal(verdict(0, 0, null, NOW)[0], 'quiet');
});
