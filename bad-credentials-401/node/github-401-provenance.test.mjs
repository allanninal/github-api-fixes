import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  diagnose, fromGithub, messageOf, rung,
} from './github-401-provenance.mjs';

const gh = (status, message = null, login = null, github = true) =>
  ({ status, message, login, github });

test('the two messages get different symbols', () => {
  assert.equal(rung(401, 'bad credentials'), 'rejected');
  assert.equal(rung(401, 'requires authentication'), 'anonymous');
});

test('a 401 with neither message is not forced into one', () => {
  assert.equal(rung(401, null), 'unlabelled-401');
  assert.equal(rung(401, 'something new'), 'unlabelled-401');
});

test('the ordinary statuses reduce predictably', () => {
  assert.equal(rung(200, null), 'ok');
  assert.equal(rung(204, null), 'ok');
  assert.equal(rung(403, 'forbidden'), 'forbidden');
  assert.equal(rung(404, null), 'http-404');
  assert.equal(rung(0, null), 'error');
  assert.equal(rung(null, null), 'error');
});

test('the message is only read from a json object', () => {
  assert.equal(messageOf({ message: '  Bad Credentials ' }), 'bad credentials');
  assert.equal(messageOf({ message: '' }), null);
  assert.equal(messageOf('<html>401</html>'), null);
  assert.equal(messageOf(null), null);
});

test("github's furniture is recognised whatever the header case", () => {
  assert.deepEqual(fromGithub({ 'X-GitHub-Request-Id': 'ABC:123' }),
    [true, 'x-github-request-id']);
  assert.equal(fromGithub({ Server: 'github.com' })[0], true);
  assert.deepEqual(fromGithub({ server: 'squid/5.7' }), [false, null]);
  assert.deepEqual(fromGithub({}), [false, null]);
});

test('a 401 without github furniture is an intermediary', () => {
  const [state, detail] = diagnose(gh(401, 'bad credentials', null, false),
    gh(200), gh(401, 'bad credentials', null, false));
  assert.equal(state, 'not-github');
  assert.match(detail, /Re-minting will not help/);
});

test('a refused control stops the diagnosis', () => {
  const [state] = diagnose(gh(401, 'bad credentials'), gh(403, 'forbidden'), gh(401));
  assert.equal(state, 'anonymous-refused');
});

test('a control that could not be made is its own state', () => {
  assert.equal(diagnose(gh(401, 'bad credentials'), gh(0), gh(401))[0], 'no-baseline');
});

test('rejected on a public endpoint is the credential', () => {
  const [state, detail] = diagnose(gh(401, 'bad credentials'), gh(200),
    gh(401, 'bad credentials'));
  assert.equal(state, 'credential-rejected');
  assert.match(detail, /200 without the header and 401 with it/);
});

test('requires authentication means the header never arrived', () => {
  const [state, detail] = diagnose(gh(200), gh(200), gh(401, 'requires authentication'));
  assert.equal(state, 'header-not-arriving');
  assert.match(detail, /carried nothing/);
});

test('a credential accepted on one path and refused on another', () => {
  const [state] = diagnose(gh(200), gh(200), gh(401, 'bad credentials'));
  assert.equal(state, 'path-dependent');
});

test('a valid credential for the wrong account is a failure', () => {
  const [state, detail] = diagnose(gh(200), gh(200), gh(200, null, 'someone-else'),
    'acme-ci-bot');
  assert.equal(state, 'wrong-account');
  assert.match(detail, /someone-else/);
});

test('the login comparison ignores case', () => {
  assert.equal(diagnose(gh(200), gh(200), gh(200, null, 'Acme-CI-Bot'),
    'acme-ci-bot')[0], 'credential-valid');
});

test('a 403 on user is not a bad credential', () => {
  const [state, detail] = diagnose(gh(200), gh(200), gh(403, 'forbidden'));
  assert.equal(state, 'authenticated-but-forbidden');
  assert.match(detail, /SSO/);
});

test('a working credential sends you to look elsewhere', () => {
  const [state, detail] = diagnose(gh(200), gh(200), gh(200, null, 'acme-ci-bot'));
  assert.equal(state, 'credential-valid');
  assert.match(detail, /acme-ci-bot/);
});

test('probes that disagree are reported as unclear rather than guessed', () => {
  assert.equal(diagnose(gh(500), gh(200), gh(500))[0], 'unclear');
});
