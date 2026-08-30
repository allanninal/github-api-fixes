import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  ENDPOINT_FEATURES, PLAN_DEPENDENT, classify, featureFor, flagState, matrix,
  normaliseEndpoint, planMayBeTheConstraint, readCost, repair, securityBlock,
  statusMatches,
} from './github-feature-flags.mjs';

const ADMIN_VIEW = {
  private: true,
  visibility: 'private',
  has_issues: false,
  has_wiki: true,
  security_and_analysis: {
    advanced_security: { status: 'disabled' },
    secret_scanning: { status: 'disabled' },
    secret_scanning_push_protection: { status: 'disabled' },
    dependabot_security_updates: { status: 'enabled' },
  },
};
const READER_VIEW = {
  private: true, visibility: 'private', has_issues: false, has_wiki: true,
};
const HEALTHY = {
  private: false,
  visibility: 'public',
  has_issues: true,
  security_and_analysis: {
    advanced_security: { status: 'enabled' },
    secret_scanning: { status: 'enabled' },
  },
};

test('one off switch produces three status codes', () => {
  assert.equal(featureFor('/code-scanning/alerts').status_when_disabled, 403);
  assert.equal(featureFor('/secret-scanning/alerts').status_when_disabled, 404);
  assert.equal(featureFor('/issues').status_when_disabled, 410);
});

test('a logged url is reduced to a table key', () => {
  assert.equal(
    normaliseEndpoint('https://api.github.com/repos/octo/pay/code-scanning/alerts?state=open'),
    '/code-scanning/alerts',
  );
  assert.equal(normaliseEndpoint('/repos/octo/pay/issues'), '/issues');
  assert.equal(normaliseEndpoint('issues'), '/issues');
  assert.equal(normaliseEndpoint(''), '');
});

test('an absent security block is unreported and never disabled', () => {
  assert.equal(securityBlock(READER_VIEW), null);
  assert.equal(flagState(READER_VIEW, 'advanced_security', 'security'), 'unreported');
  assert.equal(flagState(ADMIN_VIEW, 'advanced_security', 'security'), 'disabled');
  assert.equal(flagState(ADMIN_VIEW, 'dependabot_security_updates', 'security'), 'enabled');
});

test('a toggle is readable by anybody who can read the repo', () => {
  assert.equal(flagState(READER_VIEW, 'has_issues', 'toggle'), 'disabled');
  assert.equal(flagState(READER_VIEW, 'has_wiki', 'toggle'), 'enabled');
  assert.equal(flagState(READER_VIEW, 'has_discussions', 'toggle'), 'unreported');
});

test('a disabled feature is named and no permission opens it', () => {
  const [state, detail] = classify(ADMIN_VIEW, featureFor('/code-scanning/alerts'), 403);
  assert.equal(state, 'feature-disabled');
  assert.match(detail, /No permission opens it/);
});

test('the unreported case blames the readers role not the repo', () => {
  const row = featureFor('/code-scanning/alerts');
  const [state, detail] = classify(READER_VIEW, row, 403);
  assert.equal(state, 'feature-unreported');
  assert.match(detail, /admin on the repository/);
  assert.match(repair(state, row), /absent block is a limit on your reading/);
});

test('an enabled feature with a named permission is somebody elses note', () => {
  const row = featureFor('/code-scanning/alerts');
  const [state, detail] = classify(HEALTHY, row, 403, 'security_events=read');
  assert.equal(state, 'permission-named');
  assert.match(detail, /security_events=read/);
  assert.equal(classify(HEALTHY, row, 403, '')[0], 'feature-enabled');
});

test('a status that does not match is called a mismatch', () => {
  const row = featureFor('/secret-scanning/alerts');
  assert.equal(statusMatches(row, 404), true);
  assert.equal(statusMatches(row, 403), false);
  assert.equal(statusMatches(row, null), null);
  const [state, detail] = classify(ADMIN_VIEW, row, 403);
  assert.equal(state, 'status-mismatch');
  assert.match(detail, /404/);
});

test('the issues toggle answers 410 gone which reads as deprecation', () => {
  const [state, detail] = classify(ADMIN_VIEW, featureFor('/issues'), 410);
  assert.equal(state, 'feature-disabled');
  assert.match(detail, /410/);
});

test('the matrix covers every endpoint in the table', () => {
  const rows = matrix(ADMIN_VIEW);
  assert.equal(rows.length, Object.keys(ENDPOINT_FEATURES).length);
  const byEndpoint = Object.fromEntries(rows.map((r) => [r.endpoint, r]));
  assert.equal(byEndpoint['/issues'].will_serve, false);
  assert.equal(byEndpoint['/dependabot/alerts'].will_serve, true);
});

test('a proxy mapping is flagged as one', () => {
  assert.equal(featureFor('/dependabot/alerts').confidence, 'proxy');
  assert.equal(featureFor('/secret-scanning/alerts').confidence, 'exact');
  const row = featureFor('/dependabot/alerts');
  row.state = 'disabled';
  assert.match(repair('feature-disabled', row, ADMIN_VIEW), /not proof/);
});

test('the plan can be a repair an admin cannot make', () => {
  assert.ok(PLAN_DEPENDENT.includes('advanced_security'));
  assert.equal(planMayBeTheConstraint(ADMIN_VIEW, 'advanced_security'), true);
  assert.equal(planMayBeTheConstraint(HEALTHY, 'advanced_security'), false);
  assert.equal(planMayBeTheConstraint(ADMIN_VIEW, 'has_issues'), false);
  assert.match(
    repair('feature-disabled', featureFor('/code-scanning/alerts'), ADMIN_VIEW),
    /depends on the plan/,
  );
});

test('an endpoint outside the table is handed back', () => {
  assert.equal(featureFor('/pulls'), null);
  assert.equal(classify(ADMIN_VIEW, null, 403)[0], 'endpoint-unknown');
});

test('the run costs one read plus any probes', () => {
  assert.equal(readCost(), 1);
  assert.equal(readCost(7), 8);
});
