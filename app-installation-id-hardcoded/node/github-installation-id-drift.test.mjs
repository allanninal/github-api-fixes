import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  accountOf, currentIdFor, drift, indexByAccount, indexById, parseMap,
  reinstalledSince, repair, stableKey, summarize, unmapped,
} from './github-installation-id-drift.mjs';

const ACME = {
  id: 55120044, account: { login: 'acme-corp' }, created_at: '2026-08-25T08:02:11Z',
};
const GAMMA = {
  id: 41234568, account: { login: 'gamma-labs' }, created_at: '2024-02-01T00:00:00Z',
};
const BY_ID = indexById([ACME, GAMMA]);
const BY_ACCOUNT = indexByAccount([ACME, GAMMA]);

test('the map is read as pairs or as JSON', () => {
  assert.deepEqual(parseMap('acme-corp=41234567,beta-inc=41234568'),
    { 'acme-corp': '41234567', 'beta-inc': '41234568' });
  assert.deepEqual(parseMap(' {"Acme-Corp": 41234567} '), { 'acme-corp': '41234567' });
  assert.deepEqual(parseMap(' acme-corp = 41234567 ; beta-inc=9 '),
    { 'acme-corp': '41234567', 'beta-inc': '9' });
  assert.deepEqual(parseMap(''), {});
  assert.deepEqual(parseMap('nonsense'), {});
  assert.deepEqual(parseMap('{not json'), {});
});

test('ids are indexed as text however they arrived', () => {
  assert.equal(BY_ID['55120044'], ACME);
  assert.equal(indexById([{ id: '77', account: { login: 'x' } }])['77'].id, '77');
  assert.deepEqual(indexById([{ account: { login: 'x' } }]), {});
});

test('the stable key is the login not the id', () => {
  assert.equal(stableKey(ACME), 'acme-corp');
  assert.equal(stableKey({ id: 5, account: { login: 'Acme-Corp' } }), 'acme-corp');
  assert.equal(stableKey({ id: 5 }), null);
  assert.equal(accountOf(null), null);
});

test('an id that belongs to another account is its own finding', () => {
  const [state, detail] = drift('beta-inc', 41234568, BY_ID, BY_ACCOUNT);
  assert.equal(state, 'crossed');
  assert.match(detail, /gamma-labs/);
  assert.match(detail, /wrong account/);
  assert.match(repair(state, 'beta-inc'), /stop the deploy/);
});

test('a missing id on a live account names the current one', () => {
  const [state, detail] = drift('acme-corp', 41234567, BY_ID, BY_ACCOUNT);
  assert.equal(state, 'stale');
  assert.match(detail, /55120044/);
  assert.equal(currentIdFor('ACME-CORP', BY_ACCOUNT), '55120044');
});

test('a missing id on a missing account is not a stale id', () => {
  const [state, detail] = drift('delta-ltd', 999, BY_ID, BY_ACCOUNT);
  assert.equal(state, 'gone');
  assert.match(detail, /no installation on that account/);
});

test('a matching id is current whether it was stored as text', () => {
  assert.equal(drift('acme-corp', '55120044', BY_ID, BY_ACCOUNT)[0], 'current');
  assert.equal(drift('Acme-Corp', 55120044, BY_ID, BY_ACCOUNT)[0], 'current');
});

test('a reinstall after the map was written is flagged even when it matches', () => {
  const [state, detail] = drift('acme-corp', 55120044, BY_ID, BY_ACCOUNT,
    '2026-01-01T00:00:00Z');
  assert.equal(state, 'current-but-reinstalled');
  assert.match(detail, /removed and re-added/);
});

test('an unreadable date is a third answer and not a no', () => {
  assert.equal(reinstalledSince(ACME, '2026-01-01T00:00:00Z'), true);
  assert.equal(reinstalledSince(ACME, '2026-12-01T00:00:00Z'), false);
  assert.equal(reinstalledSince(ACME, null), null);
  assert.equal(reinstalledSince({}, '2026-01-01T00:00:00Z'), null);
});

test('installations the map never mentions are listed separately', () => {
  assert.deepEqual(unmapped(BY_ACCOUNT, { 'acme-corp': '1' }), ['gamma-labs']);
  assert.deepEqual(unmapped(BY_ACCOUNT, { 'ACME-CORP': '1', 'gamma-labs': '2' }), []);
});

test('the summary counts the silent finding apart', () => {
  const stats = summarize([{ state: 'crossed' }, { state: 'stale' }, { state: 'current' }]);
  assert.equal(stats.total, 3);
  assert.equal(stats.silent, 1);
  assert.equal(stats.by_state.stale, 1);
});
