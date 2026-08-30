import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  bucket, summarize, triage, verdict,
} from './github-hook-delivery-audit.mjs';

const delivery = (code, status = 'failure', when = '2026-08-01T10:00:00Z',
  id = 1, redelivery = false) =>
  ({ id, status, status_code: code, delivered_at: when, redelivery });

test('two hundred is the only success', () => {
  assert.equal(bucket(delivery(200, 'OK')), 'ok');
  assert.equal(bucket(delivery(204, 'OK')), 'ok');
});

test('no status code is unreachable, not a server error', () => {
  assert.equal(bucket(delivery(0)), 'unreachable');
  assert.equal(bucket({ status: 'failure' }), 'unreachable');
});

test('a timeout is its own bucket whatever the code says', () => {
  assert.equal(bucket({ status: 'timed out', status_code: 0 }), 'timeout');
});

test('auth failures are separated from other client errors', () => {
  assert.equal(bucket(delivery(401)), 'rejected');
  assert.equal(bucket(delivery(403)), 'rejected');
  assert.equal(bucket(delivery(404)), 'client-error');
  assert.equal(bucket(delivery(502)), 'server-error');
});

test('triage treats a null code as never delivered', () => {
  const [state, detail] = triage({ last_response: { code: null, status: 'unused' } });
  assert.equal(state, 'never');
  assert.match(detail, /no delivery/);
});

test('triage reads the failing code and message', () => {
  const [state, detail] = triage({ last_response: { code: 502, message: 'Bad Gateway' } });
  assert.equal(state, 'failing');
  assert.match(detail, /502: Bad Gateway/);
});

test('summarize keeps both ends of the window', () => {
  const s = summarize([
    delivery(200, 'OK', '2026-08-01T10:00:00Z'),
    delivery(500, 'failure', '2026-08-02T10:00:00Z', 2),
    delivery(500, 'failure', '2026-08-03T10:00:00Z', 3, true),
  ]);
  assert.equal(s.total, 3);
  assert.equal(s.failed, 2);
  assert.equal(s.first_failed, '2026-08-02T10:00:00Z');
  assert.equal(s.last_failed, '2026-08-03T10:00:00Z');
  assert.equal(s.last_ok, '2026-08-01T10:00:00Z');
  assert.equal(s.redeliveries, 1);
  assert.deepEqual(s.guids['server-error'], [2, 3]);
});

test('an empty log is not a healthy hook', () => {
  assert.equal(verdict(summarize([]))[0], 'empty');
});

test('failures older than the last success are already fixed', () => {
  const s = summarize([
    delivery(500, 'failure', '2026-08-01T10:00:00Z'),
    delivery(200, 'OK', '2026-08-02T10:00:00Z', 2),
  ]);
  const [state, detail] = verdict(s);
  assert.equal(state, 'recovered');
  assert.match(detail, /replay/);
});

test('the dominant bucket names the repair', () => {
  const s = summarize([delivery(500), delivery(500, 'failure', '2026-08-01T10:00:00Z', 2),
    delivery(404, 'failure', '2026-08-01T10:00:00Z', 3)]);
  const [state, detail] = verdict(s);
  assert.equal(state, 'server-error');
  assert.match(detail, /handler/);
});

test('a run of 401s points at the secret without claiming to read it', () => {
  const s = summarize([delivery(401), delivery(401, 'failure', '2026-08-01T10:00:00Z', 2)]);
  const [state, detail] = verdict(s);
  assert.equal(state, 'rejected');
  assert.match(detail, /will not compare secrets/);
});
