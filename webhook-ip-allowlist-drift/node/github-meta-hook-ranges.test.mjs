import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  allowsEverything, arrayScore, audit, bestOtherArray, coverage,
  coveredAddresses, merge, overlap, parseCidr, readAllowlist, repair, sizeOf,
  uncovered, verdict,
} from './github-meta-hook-ranges.mjs';

const META = {
  hooks: ['192.30.252.0/22', '140.82.112.0/20', '2a0a:a440::/29'],
  api: ['10.10.0.0/16', '10.20.0.0/16'],
  web: ['10.30.0.0/16'],
};

const parsed = (...entries) => entries.map((e) => parseCidr(e));

test('a CIDR is parsed into a range of addresses', () => {
  const range = parseCidr('192.30.252.0/22');
  assert.equal(range.version, 4);
  assert.equal(sizeOf(range), 1024n);
});

test('host bits and bare addresses are tolerated', () => {
  assert.deepEqual(parseCidr('192.30.252.7/22'), parseCidr('192.30.252.0/22'));
  assert.equal(sizeOf(parseCidr('140.82.112.5')), 1n);
  assert.equal(parseCidr('not-an-address'), null);
  assert.equal(parseCidr('   '), null);
  assert.equal(parseCidr('# a comment'), null);
});

test('ipv6 ranges are understood, compressed and expanded alike', () => {
  const range = parseCidr('2a0a:a440::/29');
  assert.equal(range.version, 6);
  assert.ok(range.end > range.start);
  assert.equal(parseCidr('2a0a:a440:0:0:0:0:0:0/29').start, range.start);
  assert.equal(parseCidr('2a0a:a440::zz/29'), null);
});

test('the two families never cover each other', () => {
  assert.equal(overlap(parseCidr('0.0.0.0/0'), parseCidr('2a0a:a440::/29')), null);
  assert.deepEqual(coverage(parseCidr('2a0a:a440::/29'), parsed('0.0.0.0/0')), ['none', 0]);
});

test('a subset is partial with the fraction it permits', () => {
  const [state, fraction] = coverage(parseCidr('192.30.252.0/22'), parsed('192.30.252.0/24'));
  assert.equal(state, 'partial');
  assert.equal(fraction, 0.25);
});

test('a superset is full coverage, not a mismatch', () => {
  assert.deepEqual(coverage(parseCidr('140.82.112.0/20'), parsed('140.82.0.0/16')), ['full', 1]);
});

test('overlapping rules are never counted twice', () => {
  const published = parseCidr('192.30.252.0/22');
  const allowed = parsed('192.30.252.0/24', '192.30.252.0/23');
  assert.equal(coveredAddresses(published, allowed), 512n);
  assert.equal(coverage(published, allowed)[0], 'partial');
});

test('adjacent rules add up to full coverage', () => {
  const published = parseCidr('192.30.252.0/23');
  const allowed = parsed('192.30.252.0/24', '192.30.253.0/24');
  assert.deepEqual(coverage(published, allowed), ['full', 1]);
  assert.equal(merge(allowed.map((a) => overlap(published, a))).length, 1);
});

test('a default route is recognised in both families', () => {
  assert.ok(allowsEverything(parsed('0.0.0.0/0')));
  assert.ok(allowsEverything(parsed('::/0')));
  assert.ok(!allowsEverything(parsed('10.0.0.0/8')));
});

test('unreadable allow-list lines are returned, not swallowed', () => {
  const [ranges, unreadable] = readAllowlist([
    '192.30.252.0/22', '  # comment', '', '140.82.112.0/20 # inline', 'hooks.github.com',
  ]);
  assert.equal(ranges.length, 2);
  assert.deepEqual(unreadable, ['hooks.github.com']);
});

test('the audit names every published range', () => {
  const rows = audit(META.hooks, parsed('140.82.112.0/20'));
  assert.deepEqual(rows.map(([, state]) => state), ['none', 'full', 'none']);
  assert.deepEqual(uncovered(rows), ['192.30.252.0/22', '2a0a:a440::/29']);
});

test('drift is the finding when some ranges are short', () => {
  const allowed = parsed('192.30.252.0/24', '140.82.112.0/20', '2a0a:a440::/29');
  const [state, detail] = verdict(META, allowed);
  assert.equal(state, 'drifted');
  assert.match(detail, /1 of 3/);
  assert.match(detail, /intermittently/);
});

test('a list built from the wrong array is named as such', () => {
  const allowed = parsed('10.10.0.0/16', '10.20.0.0/16');
  const [state, detail] = verdict(META, allowed);
  assert.equal(state, 'wrong-array');
  assert.match(detail, /api/);
  assert.equal(arrayScore(META, allowed, 'api'), 1);
  assert.equal(bestOtherArray(META, allowed)[0], 'api');
});

test('a default route passes the arithmetic and still fails the audit', () => {
  const [state, detail] = verdict(META, parsed('0.0.0.0/0', '::/0'));
  assert.equal(state, 'allow-all');
  assert.match(detail, /not filtering/);
  assert.match(repair('allow-all'), /never was/);
});

test('a complete allow-list is current', () => {
  const allowed = parsed('192.30.252.0/22', '140.82.112.0/20', '2a0a:a440::/29');
  assert.equal(verdict(META, allowed)[0], 'current');
});

test('unparsed entries downgrade a clean result', () => {
  const allowed = parsed('192.30.252.0/22', '140.82.112.0/20', '2a0a:a440::/29');
  const [state, detail] = verdict(META, allowed, 2);
  assert.equal(state, 'current-with-gaps');
  assert.match(detail, /2 allow-list entries/);
});

test('an empty allow-list is reported rather than scored', () => {
  assert.equal(verdict(META, [])[0], 'no-allowlist');
  assert.equal(verdict({}, parsed('10.0.0.0/8'))[0], 'no-hooks-array');
});

test('the repair for drift is automation and not a fresh paste', () => {
  assert.match(repair('drifted'), /on a schedule/);
  assert.match(repair('wrong-array'), /hooks array/);
});
