import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  DOCS_INDEX, ROUTE_TABLE, SAFE_VERBS, classifyNotFound, docsUrlKind,
  documentationUrlOf, getProbeIsEvidence, matchRoute, pathShapeProblem,
  permissionsHeaderHint, probeRefusal, readCost, repair, rootMapCovers,
  verbVerdict, verdict,
} from './github-route-or-verb.mjs';

const ROUTED_404 = {
  message: 'Not Found',
  documentation_url: 'https://docs.github.com/rest/repos/repos#get-a-repository',
  status: '404',
};
const UNROUTED_404 = {
  message: 'Not Found',
  documentation_url: 'https://docs.github.com/rest',
  status: '404',
};
const ROOT_MAP = {
  current_user_url: 'https://api.github.com/user',
  repository_url: 'https://api.github.com/repos/{owner}/{repo}',
  emojis_url: 'https://api.github.com/emojis',
};

test('the documentation_url is the discriminator', () => {
  assert.equal(docsUrlKind(documentationUrlOf(ROUTED_404))[0], 'endpoint-specific');
  assert.equal(docsUrlKind(documentationUrlOf(UNROUTED_404))[0], 'generic');
  assert.equal(docsUrlKind(`${DOCS_INDEX}/`)[0], 'generic');
  assert.equal(docsUrlKind(null)[0], 'absent');
  assert.equal(docsUrlKind('https://example.invalid/docs')[0], 'unrecognised');
});

test('a routed 404 is somebody elses note', () => {
  const [state, detail] = classifyNotFound(404, ROUTED_404);
  assert.equal(state, 'route-matched-resource-missing');
  assert.match(detail, /different note/);
  assert.equal(verdict(state, 'clean', 'verb-not-on-this-route')[0], 'resource-not-routing');
});

test('an unrouted 404 keeps the investigation here', () => {
  assert.equal(classifyNotFound(404, UNROUTED_404)[0], 'nothing-routed-here');
  assert.equal(classifyNotFound(200, null)[0], 'route-answers-get');
  assert.equal(classifyNotFound(401, {})[0], 'unauthenticated');
  assert.equal(classifyNotFound(403, {})[0], 'refused-not-missing');
  assert.equal(classifyNotFound(502, {})[0], 'unexpected-status');
});

test('the trailing slash that is invisible in review', () => {
  const [state, detail] = pathShapeProblem('/repos/acme/payments/');
  assert.equal(state, 'trailing-slash');
  assert.match(detail, /documents it as a cause of 404/);
  assert.equal(pathShapeProblem('/repos/acme/payments')[0], 'clean');
  assert.equal(pathShapeProblem('/repos/acme/payments?per_page=1')[0], 'clean');
});

test('the other documented shape errors', () => {
  assert.equal(pathShapeProblem('/repos/{owner}/payments')[0], 'placeholder-not-substituted');
  assert.equal(pathShapeProblem('/repos//payments')[0], 'doubled-slash');
  assert.equal(pathShapeProblem('/repos/acme/my payments')[0], 'unencoded-space');
  assert.equal(pathShapeProblem('https://api.github.com/user')[0], 'full-url-not-path');
  assert.equal(pathShapeProblem('repos/acme/payments')[0], 'no-leading-slash');
  assert.equal(pathShapeProblem('')[0], 'empty-path');
});

test('the matcher is segment wise so a smuggled slash does not match', () => {
  const [template, verbs] = matchRoute('/repos/acme/payments/collaborators/dana');
  assert.equal(template, '/repos/{owner}/{repo}/collaborators/{username}');
  assert.deepEqual([...verbs].sort(), ['delete', 'get', 'put']);
  assert.equal(matchRoute('/repos/acme/payments/branches/release/1.0/protection')[0], null);
  assert.equal(matchRoute('/repos/acme/payments/nothing-like-this')[0], null);
});

test('the wrong verb is named with the documented one', () => {
  const [state, detail] = verbVerdict('/repos/acme/payments/collaborators/dana', 'post');
  assert.equal(state, 'verb-not-on-this-route');
  assert.match(detail, /you sent POST/);
  assert.match(detail, /PUT/);
  assert.equal(verbVerdict('/repos/acme/payments/topics', 'put')[0], 'verb-is-documented');
  assert.equal(verbVerdict('/some/unknown/path', 'put')[0], 'route-not-in-table');
});

test('a get probe cannot prove a route with no get', () => {
  const [state, detail] = getProbeIsEvidence('/repos/acme/payments/merges');
  assert.equal(state, 'probe-cannot-decide');
  assert.match(detail, /proves nothing/);
  assert.equal(getProbeIsEvidence('/repos/acme/payments/topics')[0], 'probe-decides');
  assert.equal(getProbeIsEvidence('/nope')[0], 'unknown-route');
});

test('the script refuses to probe with a write and says both reasons', () => {
  const [state, detail] = probeRefusal('put');
  assert.equal(state, 'will-not-probe');
  assert.match(detail, /would be a write/);
  assert.match(detail, /returns no information/);
  assert.equal(probeRefusal('get')[0], 'safe-to-send');
  assert.equal(probeRefusal('head')[0], 'safe-to-send');
  assert.deepEqual([...SAFE_VERBS].sort(), ['get', 'head']);
});

test('no route in the table is missing its note', () => {
  for (const [template, verbs, note] of ROUTE_TABLE) {
    assert.ok(template.startsWith('/'), template);
    assert.ok(verbs.length, template);
    assert.ok(note, template);
    assert.ok(verbs.every((v) => v === v.toLowerCase()), template);
  }
});

test('the verdict puts path shape before the verb', () => {
  assert.equal(
    verdict('nothing-routed-here', 'trailing-slash', 'verb-not-on-this-route')[0],
    'path-shape-wrong',
  );
  assert.equal(verdict('nothing-routed-here', 'clean', 'verb-not-on-this-route')[0], 'wrong-verb');
  assert.equal(
    verdict('route-answers-get', 'clean', 'verb-is-documented')[0],
    'route-and-verb-both-fine',
  );
  assert.equal(
    verdict('nothing-routed-here', 'clean', 'verb-is-documented')[0],
    'route-absent-or-wrong-host',
  );
});

test('the permission header is corroboration and says so', () => {
  const [state, detail] = permissionsHeaderHint({ 'X-Accepted-GitHub-Permissions': 'issues=read' });
  assert.equal(state, 'permissions-were-evaluated');
  assert.match(detail, /Corroboration only/);
  assert.equal(permissionsHeaderHint({})[0], 'no-permission-header');
  assert.match(permissionsHeaderHint({})[1], /too weak/);
});

test('the root map is a hint and admits its coverage', () => {
  assert.equal(rootMapCovers(ROOT_MAP, '/repos/acme/payments')[0], 'family-known');
  const [state, detail] = rootMapCovers(ROOT_MAP, '/packages/npm/thing');
  assert.equal(state, 'family-not-in-map');
  assert.match(detail, /hint and not a finding/);
  assert.equal(rootMapCovers({}, '/repos/a/b')[0], 'root-unread');
});

test('the repair names the verb and does not send it', () => {
  const fix = repair('wrong-verb', '/repos/acme/payments/collaborators/dana', 'post');
  assert.match(fix, /send PUT or DELETE/);
  assert.match(fix, /Nothing here sends it/);
  assert.match(repair('route-absent-or-wrong-host', '/x', 'get'), /wrong GitHub installation/);
  assert.match(repair('undetermined', '/x', 'put'), /Do not send the verb/);
});

test('the read cost is known before anything is spent', () => {
  assert.equal(readCost(false), 1);
  assert.equal(readCost(true), 2);
});
