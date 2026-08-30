import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  REQUESTS_PER_BRANCH, coverage, instrumentVerdict, isAbsence, pushAllowlist,
  readCost, refusedByRules, refusedWrites, repair, rulesetsNamed, splitTarget,
  verdict, visibility,
} from './github-branch-protection-audit.mjs';

const ADMIN_403 = 'Must have admin rights to Repository.';
const ABSENT_404 = 'Branch not protected';

const PROTECTION = {
  required_pull_request_reviews: { required_approving_review_count: 2 },
  required_status_checks: { strict: true, contexts: ['build', 'lint'] },
  enforce_admins: { enabled: true },
  restrictions: {
    users: [{ login: 'release-bot' }], teams: [{ slug: 'platform' }], apps: [],
  },
  required_signatures: { enabled: false },
  allow_force_pushes: { enabled: false },
  allow_deletions: { enabled: false },
};

const RULES = [
  { type: 'pull_request', ruleset_id: 42, ruleset_source: 'acme' },
  { type: 'non_fast_forward', ruleset_id: 42, ruleset_source: 'acme' },
];

test('a 403 is never evidence that a branch is unprotected', () => {
  assert.equal(isAbsence(403, ADMIN_403), false);
  assert.equal(isAbsence(403, ABSENT_404), false);
  assert.equal(visibility(403, ADMIN_403), 'admin-required');
});

test('only a 404 that names the reason is an absence', () => {
  assert.equal(isAbsence(404, ABSENT_404), true);
  assert.equal(isAbsence(404, 'Not Found'), false);
  assert.equal(visibility(404, ABSENT_404), 'not-protected');
  assert.equal(visibility(404, 'Not Found'), 'ambiguous-404');
});

test('the three outcomes stay three', () => {
  assert.equal(visibility(200, ''), 'readable');
  assert.equal(visibility(500, ''), 'unknown');
  assert.equal(visibility(null, ''), 'unknown');
});

test('a refused read on a protected branch is protected and unmeasured', () => {
  const [state, detail] = verdict(true, 403, ADMIN_403, RULES);
  assert.equal(state, 'protected-rules-hidden');
  assert.match(detail, /not readable by this token/);
  assert.match(detail, /2 ruleset rule\(s\)/);
  assert.match(repair(state), /administration: read/);
});

test('a protected branch with readable rules is the measured case', () => {
  assert.equal(verdict(true, 200, '', [])[0], 'protected-rules-readable');
});

test('an unprotected branch needs both readings to agree', () => {
  assert.equal(verdict(false, 404, ABSENT_404, [])[0], 'unprotected-confirmed');
  const [state, detail] = verdict(false, 403, ADMIN_403, []);
  assert.equal(state, 'unprotected-by-flag');
  assert.match(detail, /visible without admin/);
  assert.match(repair(state), /already see/);
});

test('a ruleset protects a branch that reports protected false', () => {
  const [state, detail] = verdict(false, 404, ABSENT_404, RULES);
  assert.equal(state, 'ruleset-only');
  assert.match(detail, /from a ruleset/);
  assert.match(repair(state), /read the ruleset/);
});

test('a branch that did not come back is not a protection finding', () => {
  const [state] = verdict(null, 404, 'Not Found', []);
  assert.equal(state, 'branch-unreadable');
  assert.match(repair(state), /triage the repository/);
});

test('the refusals are derived from fields not from a push', () => {
  const lines = refusedWrites(PROTECTION);
  assert.ok(lines.includes('a direct push is refused: 2 approving review(s) are '
    + 'required through a pull request'));
  assert.ok(lines.includes('a merge is refused until 2 status check(s) pass'));
  assert.ok(lines.includes('a merge is refused while the branch is behind its base'));
  assert.ok(lines.includes('administrators are not exempt from any of the above'));
  assert.ok(lines.includes('a push is refused for everyone except 2 listed actor(s)'));
  assert.ok(lines.includes('a force push is refused'));
  assert.ok(lines.includes('deleting the branch is refused'));
  assert.deepEqual(refusedWrites(null), []);
});

test('an unsigned commit rule is only reported when enabled', () => {
  assert.ok(!refusedWrites(PROTECTION).includes('an unsigned commit is refused'));
  const signed = { ...PROTECTION, required_signatures: { enabled: true } };
  assert.ok(refusedWrites(signed).includes('an unsigned commit is refused'));
});

test('a locked branch refuses everything', () => {
  const locked = { ...PROTECTION, lock_branch: { enabled: true } };
  assert.ok(refusedWrites(locked).includes(
    'the branch is locked, so every write is refused'));
});

test('the ruleset listing describes the same refusals without admin', () => {
  const lines = refusedByRules(RULES);
  assert.ok(lines.includes(
    'a pull request is required, so a direct push to this branch is refused'));
  assert.ok(lines.includes(
    'non-fast-forward updates are blocked, so a force push is refused'));
  assert.deepEqual(refusedByRules([]), []);
  assert.deepEqual(refusedByRules('not a list'), []);
  assert.deepEqual(rulesetsNamed(RULES), ['acme']);
});

test('the allowlist reports names and nothing else', () => {
  assert.deepEqual(pushAllowlist(PROTECTION), ['user:release-bot', 'team:platform']);
  assert.deepEqual(pushAllowlist({}), []);
  assert.deepEqual(pushAllowlist(null), []);
});

test('an unknown row never becomes an unprotected row', () => {
  const counts = coverage(['protected-rules-hidden', 'unknown',
    'branch-unreadable', 'unprotected-confirmed', 'protected-rules-readable']);
  assert.deepEqual(counts, {
    protected: 2, readable_in_detail: 1, unprotected: 1, unknown: 2,
  });
  const [state, detail] = instrumentVerdict(counts);
  assert.equal(state, 'instrument-gap');
  assert.match(detail, /2 of 5/);
});

test('a sweep with no detail says so rather than claiming a measurement', () => {
  const counts = coverage(['protected-rules-hidden', 'protected-rules-hidden']);
  const [state, detail] = instrumentVerdict(counts);
  assert.equal(state, 'coverage-only');
  assert.match(detail, /detail is absent/);
  assert.equal(instrumentVerdict({})[0], 'no-rows');
  assert.equal(instrumentVerdict(coverage(['protected-rules-readable']))[0], 'measured');
});

test('targets and cost are worked out before anything is fetched', () => {
  assert.deepEqual(splitTarget('acme/platform-api:release/2.1'),
    ['acme', 'platform-api', 'release/2.1']);
  assert.deepEqual(splitTarget('acme/platform-api'), ['acme', 'platform-api', 'main']);
  assert.equal(splitTarget('platform-api'), null);
  assert.equal(splitTarget(''), null);
  assert.equal(REQUESTS_PER_BRANCH, 3);
  assert.equal(readCost(['a', 'b']), 6);
  assert.equal(readCost([]), 0);
  assert.equal(readCost(null), 0);
});
