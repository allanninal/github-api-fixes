import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  baseRank, baseState, countFromLink, coverageState, drift, lastPageFromLink,
  orgTotal, readCost, repair, verdict,
} from './github-org-base-permission.mjs';

const LINK = '<https://api.github.com/user/repos?per_page=1&page=2>; rel="next", '
  + '<https://api.github.com/user/repos?per_page=1&page=9>; rel="last"';
const ORG = {
  default_repository_permission: 'none',
  public_repos: 12,
  total_private_repos: 400,
};

test('the last page number is the count at one per page', () => {
  assert.equal(lastPageFromLink(LINK), 9);
  const [count, how] = countFromLink(LINK, 1);
  assert.equal(count, 9);
  assert.match(how, /rel="last"/);
});

test('a single page is not a count of zero', () => {
  const [count, how] = countFromLink(null, 1);
  assert.equal(count, 1);
  assert.match(how, /single page/);
  assert.equal(countFromLink('', 0)[0], 0);
});

test('the link parse survives a url with commas in it', () => {
  const header = '<https://api.github.com/search?q=a,b&per_page=1&page=3>; rel="last"';
  assert.equal(lastPageFromLink(header), 3);
  assert.equal(lastPageFromLink('<https://x/?page=notanumber>; rel="last"'), null);
  assert.equal(lastPageFromLink('<https://x/?page=2>; rel="next"'), null);
});

test('the base permission says what it implies', () => {
  const [value, detail] = baseState(ORG);
  assert.equal(value, 'none');
  assert.match(detail, /were not added to/);
  assert.ok(baseRank('none') < baseRank('read'));
  assert.ok(baseRank('read') < baseRank('write'));
});

test('an absent base permission is unreadable not none', () => {
  const [value, detail] = baseState({ login: 'acme' });
  assert.equal(value, null);
  assert.match(detail, /unreadable rather than absent/);
  assert.equal(verdict(null, 'collapsed')[0], 'base-unreadable');
});

test('the organization total adds both halves', () => {
  const [total, detail] = orgTotal(ORG);
  assert.equal(total, 412);
  assert.match(detail, /public 12/);
  assert.equal(orgTotal({ login: 'acme' })[0], null);
});

test('coverage is graded rather than reported as a ratio', () => {
  assert.equal(coverageState(9, 412), 'collapsed');
  assert.equal(coverageState(0, 412), 'collapsed');
  assert.equal(coverageState(150, 412), 'shrunken');
  assert.equal(coverageState(300, 412), 'partial');
  assert.equal(coverageState(412, 412), 'full');
  assert.equal(coverageState(5, null), 'unknown');
  assert.equal(coverageState(0, 0), 'nothing-to-cover');
});

test('the finding names the field only when the field fits', () => {
  const [state, detail] = verdict('none', 'collapsed');
  assert.equal(state, 'base-none-implicit-access-gone');
  assert.match(detail, /never granted, only defaulted/);
});

test('a collapsed coverage under read is somebody elses problem', () => {
  const [state, detail] = verdict('read', 'collapsed');
  assert.equal(state, 'coverage-lost-elsewhere');
  assert.match(detail, /not this field/);
  assert.match(detail, /repository selection/);
});

test('explicit grants are reported as immunity', () => {
  const [state, detail] = verdict('none', 'full');
  assert.equal(state, 'base-none-explicit-grants-hold');
  assert.match(detail, /not exposed to this change/);
});

test('drift is reported in both directions', () => {
  const [state, detail] = drift('read', 'none');
  assert.equal(state, 'base-tightened');
  assert.match(detail, /re-graded every repository at once/);
  assert.equal(drift('read', 'write')[0], 'base-loosened');
  assert.equal(drift('read', 'read')[0], 'base-unchanged');
  assert.equal(drift(null, 'read')[0], 'drift-unknown');
  assert.equal(drift('read', null)[0], 'drift-unknown');
});

test('the repair refuses to recommend the easy fix', () => {
  const fix = repair('base-none-implicit-access-gone', 'acme');
  assert.match(fix, /add this account/);
  assert.match(fix, /Do not raise the base permission back/);
  assert.match(repair('coverage-lost-elsewhere', 'acme'), /still a member/);
});

test('the run costs three reads', () => {
  assert.equal(readCost(), 3);
});
