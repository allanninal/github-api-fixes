import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  actorFromMessage, classify, grantFromProbe, graphqlPatRefusals, identify,
  missingPermissions, operations, parseAcceptedPermissions, refusal, repair,
  scopeHeaderState, tokenKind, tokenPrefix, whereTheRequirementLives,
} from './github-fine-grained-pat-probe.mjs';

// Obviously fake, and short enough that nobody could mistake one for a secret.
const FG = 'github_pat_FAKE';
const CLASSIC = 'ghp_FAKE';
const APP = 'ghs_FAKE';

const REFUSED = { 'x-accepted-github-permissions': 'issues=read' };
const PAT_403 = 'Resource not accessible by personal access token';
const APP_403 = 'Resource not accessible by integration';

test('a fine grained token is known by a header that is not there', () => {
  const [kind, detail] = identify(FG, { 'x-github-api-version-selected': '2022-11-28' });
  assert.equal(kind, 'fine-grained personal access token');
  assert.match(detail, /no x-oauth-scopes header/);
  assert.equal(tokenPrefix(FG), 'github_pat_');
});

test('an empty scope header is the opposite of a missing one', () => {
  assert.equal(scopeHeaderState({ 'x-oauth-scopes': '' }), 'present-empty');
  assert.equal(scopeHeaderState({ 'X-OAuth-Scopes': 'repo' }), 'present');
  assert.equal(scopeHeaderState({ 'x-github-request-id': 'abc' }), 'absent');
  assert.equal(scopeHeaderState(null), 'absent');
  assert.equal(identify(CLASSIC, { 'x-oauth-scopes': '' })[0],
    'classic personal access token');
});

test('every documented prefix is named', () => {
  assert.ok(tokenKind(FG).startsWith('fine-grained'));
  assert.ok(tokenKind(CLASSIC).startsWith('classic'));
  assert.equal(tokenKind(APP), 'GitHub App installation token');
  assert.equal(tokenKind('nonsense'), 'unrecognised credential');
  assert.equal(tokenPrefix('nonsense'), 'none');
});

test('the message names the actor and routes the repair', () => {
  assert.equal(actorFromMessage(PAT_403), 'fine-grained-pat');
  assert.equal(actorFromMessage(APP_403), 'github-app');
  assert.equal(actorFromMessage('Although you appear to have the correct '
    + 'authorization credentials, the OAuth App is restricted'), 'oauth-app');
  assert.equal(actorFromMessage('Not Found'), null);
});

test('an app refusal is handed to the app note', () => {
  const [state] = classify(403, APP_403, {}, APP);
  assert.equal(state, 'not-this-note-app');
  assert.match(repair(state), /app-permission-missing/);
});

test('a classic token is handed to the scope note', () => {
  const [state] = classify(403, 'Must have admin rights to Repository.',
    { 'x-oauth-scopes': 'public_repo' }, CLASSIC);
  assert.equal(state, 'not-this-note-classic');
  assert.match(repair(state), /missing-oauth-scope/);
});

test('the fine grained refusal names what the endpoint accepts', () => {
  const [state, detail] = classify(403, PAT_403, REFUSED, FG);
  assert.equal(state, 'fine-grained-permission-missing');
  assert.match(detail, /issues=read/);
  assert.match(repair(state, REFUSED), /issues=read/);
});

test('an organization only refusal is not a missing permission', () => {
  const [state, detail] = classify(403, PAT_403, REFUSED, FG, true);
  assert.equal(state, 'org-resource-refused');
  assert.match(detail, /approval/);
  assert.match(repair(state), /approve this token/);
});

test('commas are alternatives and semicolons are requirements', () => {
  assert.deepEqual(parseAcceptedPermissions('issues=read'), [[['issues', 'read']]]);
  assert.deepEqual(parseAcceptedPermissions('issues=read,pull_requests=read'),
    [[['issues', 'read']], [['pull_requests', 'read']]]);
  assert.deepEqual(parseAcceptedPermissions('contents=read;pull_requests=write'),
    [[['contents', 'read'], ['pull_requests', 'write']]]);
  assert.deepEqual(parseAcceptedPermissions('metadata'), [[['metadata', 'read']]]);
  assert.deepEqual(parseAcceptedPermissions(''), []);
});

test('a probe has three outcomes because a 404 is not a no', () => {
  assert.equal(grantFromProbe(200, '')[0], 'granted');
  assert.equal(grantFromProbe(403, PAT_403)[0], 'refused');
  assert.equal(grantFromProbe(403, APP_403)[0], 'refused-other');
  assert.equal(grantFromProbe(401, 'Bad credentials')[0], 'unauthenticated');
  const [verdict, why] = grantFromProbe(404, 'Not Found');
  assert.equal(verdict, 'ambiguous');
  assert.match(why, /404-masking-403/);
  assert.equal(grantFromProbe(null, '')[0], 'error');
});

test('a 404 in the matrix is never reported as a refusal', () => {
  const [state] = classify(404, 'Not Found', {}, FG);
  assert.equal(state, 'ambiguous-404');
  assert.match(repair(state), /404-masking-403/);
});

test('the missing permission is the named one the probes refused', () => {
  const grants = { metadata: 'granted', issues: 'refused' };
  assert.deepEqual(missingPermissions(REFUSED, grants), [['issues', 'read']]);
  assert.deepEqual(missingPermissions(REFUSED, { issues: 'granted' }), []);
  assert.deepEqual(missingPermissions({}, grants), []);
});

test('the same refusal through graphql carries no header', () => {
  const body = {
    data: { repository: null },
    errors: [
      { type: 'FORBIDDEN', path: ['repository', 'issues'], message: PAT_403 },
      { type: 'NOT_FOUND', message: 'Could not resolve' },
    ],
  };
  assert.deepEqual(graphqlPatRefusals(body), [['repository.issues', PAT_403]]);
  assert.deepEqual(graphqlPatRefusals({ data: {} }), []);
  assert.match(whereTheRequirementLives('graphql'),
    /no x-accepted-github-permissions header/);
  assert.match(whereTheRequirementLives('rest'),
    /x-accepted-github-permissions header/);
});

test('the document this script sends is a read', () => {
  assert.deepEqual(
    operations('query Q { repository(owner: "a", name: "b") { issues(first: 1) { totalCount } } }'),
    ['query'],
  );
  assert.ok(refusal('mutation M { addStar(input: {}) { clientMutationId } }'));
  assert.ok(refusal('subscription S { thing { id } }'));
  assert.equal(refusal(''), 'the document contains no operation to send.');
});
