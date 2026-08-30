import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  CORE_QUOTA_PER_HOUR, ORG_PAGE_SIZE, acceptsWrites, classifyFailure, daysSince,
  explain, lifecycle, pagesFor, parseLink, quotaShare, readCostForRepos, repair,
  retryPolicy, rowFor, skipList, summarise, wastedRequests,
} from './github-archived-repo-guard.mjs';

const ARCHIVED = {
  full_name: 'acme/legacy-billing',
  archived: true,
  disabled: false,
  pushed_at: '2025-01-27T09:14:00Z',
};
const ACTIVE = {
  full_name: 'acme/platform-api',
  archived: false,
  disabled: false,
  pushed_at: '2026-08-20T09:14:00Z',
};
const DISABLED = {
  full_name: 'acme/suspended-thing', archived: false, disabled: true,
};
const BOTH = { full_name: 'acme/frozen-and-gone', archived: true, disabled: true };

test('the two booleans make four states', () => {
  assert.equal(lifecycle(ARCHIVED), 'archived');
  assert.equal(lifecycle(ACTIVE), 'active');
  assert.equal(lifecycle(DISABLED), 'disabled');
  assert.equal(lifecycle(BOTH), 'archived-and-disabled');
  assert.equal(lifecycle(null), 'unknown');
  assert.equal(lifecycle('not a repo'), 'unknown');
});

test('an archived repository can never accept a write', () => {
  assert.equal(acceptsWrites('archived'), false);
  assert.equal(acceptsWrites('archived-and-disabled'), false);
  assert.equal(acceptsWrites('disabled'), false);
  assert.equal(acceptsWrites('active'), true);
  assert.equal(acceptsWrites('unknown'), null);
});

test('the output a client needs is a policy not a status code', () => {
  assert.equal(retryPolicy('archived'), 'permanent-skip');
  assert.equal(retryPolicy('disabled'), 'permanent-skip');
  assert.equal(retryPolicy('active'), 'retry');
  assert.equal(retryPolicy('unknown'), 'unknown');
});

test('the explanation says the token is irrelevant', () => {
  assert.match(explain('archived'), /regardless of the token/);
  assert.match(explain('disabled'), /different owner/);
  assert.match(explain('archived-and-disabled'), /would still leave it disabled/);
  assert.match(explain('nonsense'), /unknown/);
});

test('a recorded refusal is attributed without being reproduced', () => {
  const [state, detail] = classifyFailure(403,
    'Repository was archived so is read-only.');
  assert.equal(state, 'archived-refusal');
  assert.match(detail, /not of your credential/);
  assert.match(repair(state), /No token, scope or App permission/);
});

test('a rate limit is the one 403 worth retrying', () => {
  assert.equal(classifyFailure(403, 'API rate limit exceeded for user ID 1')[0],
    'rate-limited');
  assert.match(repair('rate-limited'), /retry-after/);
});

test('a credential refusal is handed back to the credential', () => {
  const [state, detail] = classifyFailure(403, 'Resource not accessible by integration');
  assert.equal(state, 'credential-refusal');
  assert.match(detail, /blames the credential/);
  assert.equal(classifyFailure(404, 'Not Found')[0], 'not-found');
  assert.equal(classifyFailure(403, 'Forbidden')[0], 'forbidden-unattributed');
  assert.equal(classifyFailure('', '')[0], 'no-failure');
});

test('the retry waste is stated in requests and in quota', () => {
  assert.equal(wastedRequests(12, 3), 36);
  assert.equal(wastedRequests(12, 3, 24), 864);
  assert.equal(wastedRequests(0, 3), 0);
  assert.equal(wastedRequests(null, null), 0);
  assert.equal(quotaShare(864), 17);
  assert.equal(quotaShare(0), 0);
  assert.equal(CORE_QUOTA_PER_HOUR, 5000);
});

test('the skip list holds everything that cannot be written to', () => {
  const rows = [rowFor(ARCHIVED), rowFor(ACTIVE), rowFor(DISABLED), rowFor(BOTH)];
  assert.deepEqual(skipList(rows), ['acme/frozen-and-gone', 'acme/legacy-billing',
    'acme/suspended-thing']);
  assert.deepEqual(skipList([]), []);
  assert.deepEqual(skipList(null), []);
});

test('the summary counts a repository in both columns when it is both', () => {
  assert.deepEqual(summarise([rowFor(ARCHIVED), rowFor(ACTIVE), rowFor(BOTH)]), {
    total: 3, archived: 2, disabled: 1, writable: 1, unknown: 0,
  });
});

test('a row carries the policy and the repair together', () => {
  const row = rowFor(ARCHIVED);
  assert.equal(row.state, 'archived');
  assert.equal(row.retry_policy, 'permanent-skip');
  assert.equal(row.accepts_writes, false);
  assert.match(row.repair, /top of the write loop/);
  assert.notEqual(row.days_since_last_push, null);
});

test('an age is read from the timestamp or left alone', () => {
  const now = Date.parse('2026-08-31T00:00:00Z');
  assert.equal(daysSince('2026-08-01T00:00:00Z', now), 30);
  assert.equal(daysSince('2027-01-01T00:00:00Z', now), 0);
  assert.equal(daysSince(null), null);
  assert.equal(daysSince('not a date'), null);
});

test('the cost is worked out before anything is fetched', () => {
  assert.equal(readCostForRepos(['a', 'b', 'c']), 3);
  assert.equal(readCostForRepos([]), 0);
  assert.equal(ORG_PAGE_SIZE, 100);
  assert.equal(pagesFor(212), 3);
  assert.equal(pagesFor(100), 1);
  assert.equal(pagesFor(0), 0);
});

test('the link header survives a comma inside a url', () => {
  const header = '<https://api.github.com/orgs/acme/repos?type=all,public&page=2>; '
    + 'rel="next", <https://api.github.com/orgs/acme/repos?page=3>; rel="last"';
  const links = parseLink(header);
  assert.ok(links.next.endsWith('page=2'));
  assert.ok(links.last.endsWith('page=3'));
  assert.deepEqual(parseLink(''), {});
  assert.deepEqual(parseLink(null), {});
});
