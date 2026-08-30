import { test } from 'node:test';
import assert from 'node:assert/strict';
import { diff, parseAccepted } from './github-app-permission-diff.mjs';

test('the header parses to name and level pairs', () => {
  assert.deepEqual(parseAccepted('pull_requests=write'), [['pull_requests', 'write']]);
  assert.deepEqual(parseAccepted('contents=read, metadata=read'),
    [['contents', 'read'], ['metadata', 'read']]);
  assert.deepEqual(parseAccepted('issues=write; pull_requests=write'),
    [['issues', 'write'], ['pull_requests', 'write']]);
});

test('an absent header parses to nothing rather than a guess', () => {
  assert.deepEqual(parseAccepted(null), []);
  assert.deepEqual(parseAccepted(''), []);
  assert.deepEqual(parseAccepted('garbage-with-no-equals'), []);
});

test('a 403 with no header is not a permission problem', () => {
  const [state, detail] = diff({ contents: 'read' }, [], 403);
  assert.equal(state, 'endpoint-refuses-apps');
  assert.match(detail, /installation token/);
});

test('read where write is needed is its own state', () => {
  const [state, detail] = diff({ pull_requests: 'read' },
                               parseAccepted('pull_requests=write'));
  assert.equal(state, 'level-too-low');
  assert.match(detail, /has read and needs write/);
});

test('a permission that is absent is named', () => {
  const [state, detail] = diff({ contents: 'read' },
                               parseAccepted('pull_requests=write'));
  assert.equal(state, 'permission-absent');
  assert.match(detail, /pull_requests: write/);
});

test('holding everything asked for points elsewhere', () => {
  const [state, detail] = diff({ pull_requests: 'write', metadata: 'read' },
                               parseAccepted('pull_requests=write, metadata=read'));
  assert.equal(state, 'sufficient');
  assert.match(detail, /accepted/);
});

test('write satisfies a read requirement', () => {
  assert.equal(diff({ contents: 'write' }, parseAccepted('contents=read'))[0],
               'sufficient');
});

test('an unreadable map is not an empty one', () => {
  assert.equal(diff(null, parseAccepted('issues=write'))[0], 'needed');
  assert.equal(diff({}, parseAccepted('issues=write'))[0], 'permission-absent');
});

test('a success and a non-403 are not diffed at all', () => {
  assert.equal(diff({}, parseAccepted('issues=write'), 200)[0], 'accessible');
  const [state, detail] = diff({}, parseAccepted('issues=write'), 404);
  assert.equal(state, 'not-a-permission-error');
  assert.match(detail, /masked/);
});
