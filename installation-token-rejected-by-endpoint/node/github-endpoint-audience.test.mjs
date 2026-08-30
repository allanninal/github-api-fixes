import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  accepts, canonical, guess, substitute, verdict,
} from './github-endpoint-audience.mjs';

test('a concrete path reduces to its template', () => {
  assert.equal(canonical('/repos/acme/api/issues'), '/repos/{owner}/{repo}/issues');
  assert.equal(canonical('/users/octocat'), '/users/{username}');
});

test('query strings, fragments and slashes do not change the route', () => {
  assert.equal(canonical('/user/repos?per_page=100'), '/user/repos');
  assert.equal(canonical('/user/repos/'), '/user/repos');
  assert.equal(canonical('https://api.github.com/user/repos'), '/user/repos');
  assert.equal(canonical('user/repos'), '/user/repos');
});

test('user and users are different routes', () => {
  assert.equal(canonical('/user'), '/user');
  assert.notEqual(canonical('/users/octocat'), '/user');
});

test('an unknown path is not forced onto a template', () => {
  assert.equal(canonical('/enterprises/acme/audit-log'), null);
});

test('the route table answers only for known routes', () => {
  assert.ok(!accepts('/user').has('s2s'));
  assert.ok(accepts('/repos/{owner}/{repo}/issues').has('s2s'));
  assert.equal(accepts('/nowhere'), null);
});

test('the heuristic covers the user family and declines the rest', () => {
  const [classes] = guess('/user/blocks');
  assert.deepEqual([...classes].sort(), ['any', 'u2s']);
  const [none, reason] = guess('/enterprises/acme/audit-log');
  assert.equal(none, null);
  assert.match(reason, /not in the table/);
});

test('the heuristic knows app routes want the jwt', () => {
  const [classes] = guess('/app/hook/config');
  assert.deepEqual([...classes], ['jwt']);
});

test('a dead credential is never reported as a route problem', () => {
  const [state, detail] = verdict(false, 403, '/user', new Set(['any', 'u2s']));
  assert.equal(state, 'not-an-installation-token');
  assert.match(detail, /not the mismatch/);
});

test('a route that wants a person names that and not a permission', () => {
  const [state, detail] = verdict(true, 403, '/user', accepts('/user'));
  assert.equal(state, 'needs-user-context');
  assert.match(detail, /no permission opens it/);
});

test('a route that wants the app jwt is its own state', () => {
  const [state, detail] = verdict(true, 401, '/app', accepts('/app'));
  assert.equal(state, 'needs-app-jwt');
  assert.match(detail, /sign a fresh JWT/);
});

test('a route that does accept installation tokens is sent elsewhere', () => {
  const route = '/repos/{owner}/{repo}/hooks';
  const [state, detail] = verdict(true, 403, route, accepts(route));
  assert.equal(state, 'installation-tokens-accepted');
  assert.match(detail, /x-accepted-github-permissions/);
});

test('a successful call is not a finding', () => {
  const route = '/installation/repositories';
  assert.equal(verdict(true, 200, route, accepts(route))[0], 'endpoint-accepted');
});

test('an unknown route says so rather than guessing', () => {
  const [state, detail] = verdict(true, 403, null, null);
  assert.equal(state, 'route-unknown');
  assert.match(detail, /genuinely unknown/);
});

test('a heuristic answer is labelled as one', () => {
  const [, detail] = verdict(true, 403, null, new Set(['any', 'u2s']), true);
  assert.match(detail, /by heuristic/);
});

test('substitutes exist where there is an equivalent and not where there is not', () => {
  assert.equal(substitute('/user/repos')[0], '/installation/repositories');
  assert.equal(substitute('/gists')[0], null);
  assert.equal(substitute('/repos/{owner}/{repo}'), null);
});
