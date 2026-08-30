import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  ABSENCE_MEANING, actionable, ageDays, installationIndex, printedStep,
  probeState, productRepair, readCost, reconcile, requestAgeState,
} from './github-app-installation-pending.mjs';

const NOW = new Date('2026-08-29T12:00:00Z');

const INSTALLATIONS = [
  {
    id: 41,
    account: { login: 'Initech' },
    created_at: '2026-07-02T09:00:00Z',
    repository_selection: 'all',
    suspended_at: null,
  },
  {
    id: 42,
    account: { login: 'umbrella' },
    created_at: '2026-05-01T09:00:00Z',
    repository_selection: 'selected',
    suspended_at: '2026-08-01T09:00:00Z',
  },
];

test('the index is case insensitive because records are hand written', () => {
  const index = installationIndex(INSTALLATIONS);
  assert.equal(index.initech.id, 41);
  assert.equal(index.umbrella.suspended, true);
  assert.equal(index.initech.suspended, false);
  assert.deepEqual(installationIndex([{ id: 1 }]), {});
});

test('the probe says what a 404 does and does not mean', () => {
  const [state, detail] = probeState(404);
  assert.equal(state, 'no-installation');
  assert.ok(detail.includes(ABSENCE_MEANING));
  assert.equal(probeState(200)[0], 'installed');
  assert.equal(probeState(401)[0], 'unreadable');
});

test('a product that says connected against nothing is the headline', () => {
  const [state, detail] = reconcile(
    { account: 'globex', connected: true, started_at: '2026-08-10T00:00:00Z' },
    null, NOW,
  );
  assert.equal(state, 'false-connected');
  assert.match(detail, /globex/);
  assert.ok(detail.includes(ABSENCE_MEANING));
  assert.equal(actionable(state), true);
});

test('a fresh request and a forgotten one are different sentences', () => {
  const [fresh, freshDetail] = reconcile(
    { account: 'globex', connected: false, started_at: '2026-08-27T12:00:00Z' },
    null, NOW,
  );
  assert.equal(fresh, 'awaiting-approval');
  assert.match(freshDetail, /may simply not have looked yet/);
  const [stale, staleDetail] = reconcile(
    { account: 'globex', connected: false, started_at: '2026-07-01T12:00:00Z' },
    null, NOW,
  );
  assert.equal(stale, 'stale-request');
  assert.match(staleDetail, /notified once/);
});

test('the reconciliation runs in the other direction too', () => {
  const [state, detail] = reconcile(
    { account: 'initech', connected: false },
    installationIndex(INSTALLATIONS).initech, NOW,
  );
  assert.equal(state, 'unrecorded-installation');
  assert.match(detail, /nothing in your product noticed/);
});

test('a suspended installation is handed to its own note', () => {
  const [state, detail] = reconcile(
    { account: 'umbrella', connected: true },
    installationIndex(INSTALLATIONS).umbrella, NOW,
  );
  assert.equal(state, 'installed-but-suspended');
  assert.match(detail, /already happened/);
  assert.match(printedStep(state, 'umbrella'), /unsuspend/);
});

test('agreement in either direction is quiet', () => {
  assert.equal(reconcile({ account: 'initech', connected: true },
    installationIndex(INSTALLATIONS).initech, NOW)[0], 'agreed-connected');
  assert.equal(reconcile({ account: 'globex', connected: false }, null, NOW)[0],
    'agreed-disconnected');
  assert.equal(actionable('agreed-connected'), false);
});

test('an unaged request does not pretend to know when it started', () => {
  const [state, detail] = requestAgeState(null);
  assert.equal(state, 'age-unknown');
  assert.match(detail, /does not say when the flow started/);
  assert.equal(ageDays(null, NOW), null);
  assert.equal(ageDays('not-a-date', NOW), null);
  assert.equal(Math.round(ageDays('2026-08-27T12:00:00Z', NOW) * 10) / 10, 2.0);
});

test('the step is addressed to a human and never taken', () => {
  const step = printedStep('false-connected', 'globex');
  assert.match(step, /an owner of globex has to approve/);
  assert.match(step, /Nothing here requests or approves anything/);
  assert.equal(printedStep('agreed-connected', 'globex'), 'nothing for this account.');
});

test('the product repair is about the state machine not the api', () => {
  const fix = productRepair(['false-connected', 'agreed-connected']);
  assert.match(fix, /stop rendering a completed flow as a connection/);
  assert.match(fix, /on a schedule/);
  assert.match(productRepair(['stale-request']), /expires by neglect/);
  assert.match(productRepair(['agreed-connected']), /^nothing/);
});

test('the cost counts the list pages and one probe each', () => {
  assert.equal(readCost([{ account: 'a' }, { account: 'b' }], 1), 4);
  assert.equal(readCost([{ account: 'a' }], 3), 5);
  assert.equal(readCost([], 1), 2);
});
