import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  endpoint, group, guidPairs, overlap,
} from './github-duplicate-hooks.mjs';

const hook = (source, url, events, active = true, id = 1) =>
  ({ source, id, url, events, active });

test('endpoint ignores the ways two urls differ cosmetically', () => {
  const same = 'hooks.example.com/gh';
  assert.equal(endpoint('https://hooks.example.com/gh'), same);
  assert.equal(endpoint('https://hooks.example.com/gh/'), same);
  assert.equal(endpoint('HTTPS://Hooks.Example.com/gh'), same);
  assert.equal(endpoint('http://hooks.example.com/gh?token=x'), same);
  assert.equal(endpoint('https://hooks.example.com:8443/gh'),
    'hooks.example.com:8443/gh');
  assert.equal(endpoint(null), '');
});

test('overlap treats a wildcard as covering everything', () => {
  assert.deepEqual(overlap(['push'], ['push', 'issues']), ['push']);
  assert.deepEqual(overlap(['*'], ['push', 'issues']), ['issues', 'push']);
  assert.deepEqual(overlap(['*'], ['*']), ['*']);
  assert.deepEqual(overlap(['push'], ['issues']), []);
});

test('one url in two scopes with shared events is the finding', () => {
  const rows = group([
    hook('org acme', 'https://hooks.example.com/gh', ['push'], true, 1),
    hook('repo acme/api', 'https://hooks.example.com/gh/', ['push', 'issues'], true, 2),
  ]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].state, 'duplicate');
  assert.deepEqual(rows[0].shared, ['push']);
});

test('a deliberate split is not reported as a duplicate', () => {
  const rows = group([
    hook('org acme', 'https://hooks.example.com/gh', ['push'], true, 1),
    hook('repo acme/api', 'https://hooks.example.com/gh', ['issues'], true, 2),
  ]);
  assert.equal(rows[0].state, 'disjoint');
  assert.deepEqual(rows[0].shared, []);
});

test('an inactive second hook is latent rather than duplicate', () => {
  const rows = group([
    hook('org acme', 'https://hooks.example.com/gh', ['push'], true, 1),
    hook('repo acme/api', 'https://hooks.example.com/gh', ['push'], false, 2),
  ]);
  assert.equal(rows[0].state, 'latent');
});

test('a single hook is unique', () => {
  const rows = group([hook('repo acme/api', 'https://hooks.example.com/gh', ['push'])]);
  assert.equal(rows[0].state, 'unique');
});

test('guidPairs says whether delivery id dedup would help', () => {
  const shared = guidPairs({
    'org acme': [{ guid: 'g1', event: 'push', delivered_at: '2026-08-01T10:00:03Z' }],
    'repo acme/api': [{ guid: 'g1', event: 'push', delivered_at: '2026-08-01T10:00:03Z' }],
  });
  assert.equal(shared.shared_guids, 1);
  assert.equal(shared.same_event_different_guid, 0);

  const split = guidPairs({
    'org acme': [{ guid: 'g1', event: 'push', delivered_at: '2026-08-01T10:00:03Z' }],
    'repo acme/api': [{ guid: 'g2', event: 'push', delivered_at: '2026-08-01T10:00:04Z' }],
  });
  assert.equal(split.shared_guids, 0);
  assert.equal(split.same_event_different_guid, 1);
});
