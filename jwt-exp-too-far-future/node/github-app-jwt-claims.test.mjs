import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  CEILING, audit, claims, decodeSegment, interpret, lifetime, recommend, skew,
} from './github-app-jwt-claims.mjs';

const NOW = 1772000000;

const seg = (value) => Buffer.from(JSON.stringify(value)).toString('base64url');

/** An obviously fake JWT: real claims, and the word sig for a signature. */
const token = (payload, header = { alg: 'RS256', typ: 'JWT' }) =>
  `${seg(header)}.${seg(payload)}.sig`;

test('a segment decodes without any key', () => {
  assert.deepEqual(decodeSegment(seg({ iss: '123456' })), { iss: '123456' });
  assert.equal(decodeSegment('%%%'), null);
  assert.equal(decodeSegment(seg([1, 2])), null);
});

test('a jwt splits into a header and a payload', () => {
  const [header, payload] = claims(token({ iat: NOW, exp: NOW + 540 }));
  assert.equal(header.alg, 'RS256');
  assert.equal(payload.exp - payload.iat, 540);
});

test('something that is not three segments decodes to nothing', () => {
  assert.deepEqual(claims('abc.def'), [null, null]);
  assert.deepEqual(claims(''), [null, null]);
  assert.deepEqual(claims(null), [null, null]);
});

test('lifetime and skew are plain arithmetic', () => {
  const payload = { iat: NOW - 60, exp: NOW + 480 };
  assert.equal(lifetime(payload), 540);
  assert.equal(skew(payload, NOW), -60);
  assert.equal(lifetime({ iat: '2026-01-01', exp: NOW }), null);
  assert.equal(skew({}, NOW), null);
});

test('an hour long jwt is the headline finding', () => {
  const [state, detail] = audit({ iat: NOW, exp: NOW + 3600 }, NOW);
  assert.equal(state, 'exp-too-far-future');
  assert.match(detail, /3600s/);
  assert.match(detail, /3000s over/);
});

test('the ceiling is checked before the clock', () => {
  const [state] = audit({ iat: NOW + 600, exp: NOW + 600 + 3600 }, NOW);
  assert.equal(state, 'exp-too-far-future');
});

test('exactly the ceiling is still legal', () => {
  assert.equal(audit({ iat: NOW, exp: NOW + CEILING }, NOW)[0], 'within-ceiling');
  assert.equal(audit({ iat: NOW, exp: NOW + CEILING + 1 }, NOW)[0], 'exp-too-far-future');
});

test('a missing claim is named rather than computed around', () => {
  assert.equal(audit({ exp: NOW + 300 }, NOW)[0], 'no-iat');
  assert.equal(audit({ iat: NOW }, NOW)[0], 'no-exp');
});

test('milliseconds where seconds were expected are caught', () => {
  const [state, detail] = audit({ iat: '1772000000', exp: '1772000540' }, NOW);
  assert.equal(state, 'non-numeric-claim');
  assert.match(detail, /millisecond/);
});

test('exp before iat is its own state', () => {
  const [state, detail] = audit({ iat: NOW, exp: NOW - 10 }, NOW);
  assert.equal(state, 'exp-not-after-iat');
  assert.match(detail, /10 second\(s\) before/);
});

test('a cached jwt that ran out is told apart from a long one', () => {
  const [state, detail] = audit({ iat: NOW - 900, exp: NOW - 360 }, NOW);
  assert.equal(state, 'already-expired');
  assert.match(detail, /cached/);
});

test('a fast signing clock is reported as drift and not as the ceiling', () => {
  const [state, detail] = audit({ iat: NOW + 300, exp: NOW + 540 }, NOW);
  assert.equal(state, 'iat-in-the-future');
  assert.match(detail, /different repair/);
});

test('a jwt about to expire is flagged before it does', () => {
  assert.equal(audit({ iat: NOW - 580, exp: NOW + 20 }, NOW)[0], 'expiring-imminently');
});

test('a healthy jwt says so without qualification', () => {
  const [state, detail] = audit({ iat: NOW - 60, exp: NOW + 480 }, NOW);
  assert.equal(state, 'within-ceiling');
  assert.match(detail, /540s/);
});

test('the recommendation is a pair of numbers to paste', () => {
  const want = recommend({ iat: NOW, exp: NOW + 3600 }, NOW);
  assert.equal(want.iat, NOW - 60);
  assert.equal(want.exp, NOW + 480);
  assert.equal(want.seconds_to_remove, 3060);
});

test('the live messages map to the same states as the local check', () => {
  assert.equal(interpret(200, null)[0], 'accepted');
  assert.equal(interpret(401, "'Expiration time' claim ('exp') is too far in the future")[0],
    'exp-too-far-future');
  assert.equal(interpret(401, "'Issued at' claim ('iat') is in the future")[0],
    'iat-in-the-future');
  assert.equal(interpret(401,
    "'Expiration time' claim ('exp') must be a numeric value representing the future time")[0],
  'already-expired');
  assert.equal(interpret(401, 'A JSON web token could not be decoded')[0], 'undecodable');
  assert.equal(interpret(404, 'Integration not found')[0], 'wrong-app-or-key');
  assert.equal(interpret(403, 'Resource not accessible by integration')[0], 'unrelated');
});
