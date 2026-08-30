import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  ageDays, evidenceDirection, parseTime, reconcile, redact, repair, secretState,
  uneditedSinceCreation, verdict,
} from './github-hook-secret-age.mjs';

const NOW = new Date('2026-08-31T00:00:00Z');
const OLD = '2019-04-11T22:14:38Z';
const RECENT = '2026-08-01T09:00:00Z';
const MASKED = { url: 'https://hooks.example.com/github', secret: '********', content_type: 'json' };
const NO_SECRET = { url: 'https://hooks.example.com/github', content_type: 'json' };

test('presence is the only fact read about a secret', () => {
  assert.equal(secretState(MASKED), 'set');
  assert.equal(secretState(NO_SECRET), 'absent');
  assert.equal(secretState(null), 'unknown');
});

test('no secret value survives into the report', () => {
  const leaked = { url: 'https://hooks.example.com', secret: 'not-a-real-value' };
  const printed = JSON.stringify(redact(leaked));
  assert.ok(!printed.includes('not-a-real-value'));
  assert.equal(redact(leaked).secret, 'set');
  assert.equal(redact(MASKED).secret, 'set');
  assert.ok(!JSON.stringify(redact(MASKED)).includes('********'));
});

test('timestamps are parsed and aged in whole days', () => {
  assert.equal(parseTime(OLD).getUTCFullYear(), 2019);
  assert.equal(parseTime('2019-04-11T22:14:38+00:00').getTime(), parseTime(OLD).getTime());
  assert.equal(parseTime('nonsense'), null);
  assert.equal(parseTime(null), null);
  assert.equal(ageDays(RECENT, NOW), 29);
  assert.equal(ageDays(null, NOW), null);
});

test('a hook never edited since creation is recognised', () => {
  assert.ok(uneditedSinceCreation(OLD, OLD));
  assert.ok(uneditedSinceCreation('2019-04-11T22:14:38Z', '2019-04-11T22:14:59Z'));
  assert.ok(!uneditedSinceCreation(OLD, RECENT));
  assert.ok(!uneditedSinceCreation(null, RECENT));
});

test('the evidence only points one way', () => {
  assert.equal(evidenceDirection(2698, 180), 'conclusive');
  assert.equal(evidenceDirection(29, 180), 'inconclusive');
  assert.equal(evidenceDirection(180, 180), 'conclusive');
  assert.equal(evidenceDirection(null, 180), 'unknown');
});

test('an ancient hook is the finding', () => {
  const [state, detail] = verdict(MASKED, OLD, OLD, NOW, 180);
  assert.equal(state, 'overdue');
  assert.match(detail, /2698 days/);
  assert.match(detail, /the secret the hook was created with/);
});

test('a recent edit is not graded as compliant', () => {
  const [state, detail] = verdict(MASKED, OLD, RECENT, NOW, 180);
  assert.equal(state, 'inconclusive');
  assert.match(detail, /an edit is not a rotation/);
  assert.match(detail, /unknown rather than compliant/);
});

test('an absent secret is handed to the other note', () => {
  const [state, detail] = verdict(NO_SECRET, OLD, OLD, NOW, 180);
  assert.equal(state, 'no-secret');
  assert.match(detail, /nothing to rotate/);
  assert.match(repair('no-secret'), /Age is not the problem/);
});

test('a claimed rotation the hook predates is a finding', () => {
  assert.equal(reconcile(OLD, '2026-02-14'), 'not-applied');
  assert.equal(reconcile(RECENT, '2026-02-14'), 'consistent');
  assert.equal(reconcile(RECENT, null), 'unknown');
  const [state, detail] = verdict(MASKED, OLD, OLD, NOW, 180, '2026-02-14');
  assert.equal(state, 'rotation-not-applied');
  assert.match(detail, /2026-02-14/);
  assert.match(detail, /it was not this hook/);
});

test('a claim the hook supports does not override the age', () => {
  assert.equal(verdict(MASKED, OLD, RECENT, NOW, 180, '2026-02-14')[0], 'inconclusive');
});

test('an unreadable timestamp is admitted rather than guessed', () => {
  const [state, detail] = verdict(MASKED, OLD, 'not a date', NOW, 180);
  assert.equal(state, 'age-unknown');
  assert.match(detail, /nothing about its age/);
});

test('the repair never suggests a straight swap', () => {
  assert.match(repair('overdue'), /overlap window/);
  assert.match(repair('rotation-not-applied'), /overlap window/);
  assert.match(repair('inconclusive'), /written record/);
});
