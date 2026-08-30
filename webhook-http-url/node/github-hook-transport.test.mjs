import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  classify, hostOf, isPrivateHost, looksCompliant, repair, safeUrl,
  schemeOf, summarize,
} from './github-hook-transport.mjs';

const OPEN = {
  id: 1,
  config: {
    url: 'http://hooks.acme.io/github', insecure_ssl: '0',
    secret: '********', content_type: 'json',
  },
};
const LOCAL = {
  id: 2,
  config: { url: 'http://localhost:3000/hooks', insecure_ssl: '0' },
};
const TLS = {
  id: 3,
  config: { url: 'https://hooks.acme.io/github', insecure_ssl: '0', secret: '********' },
};
const UNVERIFIED = {
  id: 4,
  config: { url: 'https://hooks.acme.io/github', insecure_ssl: '1', secret: '********' },
};

test('plaintext on a routable host is the finding', () => {
  const [state, detail] = classify(OPEN);
  assert.equal(state, 'plaintext');
  assert.match(detail, /unencrypted connection/);
  assert.match(repair(state, OPEN), /signing payloads on an open channel/);
});

test('plaintext on localhost is a dead hook not a leak', () => {
  const [state, detail] = classify(LOCAL);
  assert.equal(state, 'plaintext-unreachable');
  assert.match(detail, /never delivered anything/);
  assert.match(repair(state, LOCAL), /delete this hook/);
});

test('the certificate question is handed to the other note', () => {
  const [state, detail] = classify(UNVERIFIED);
  assert.equal(state, 'encrypted-unverified');
  assert.match(detail, /different question/);
  assert.equal(classify(TLS)[0], 'encrypted');
});

test('the compliant looking field is named in the finding', () => {
  assert.ok(looksCompliant(OPEN));
  assert.ok(!looksCompliant(TLS));
  assert.ok(!looksCompliant(UNVERIFIED));
  assert.match(classify(OPEN)[1], /what a hook with no TLS at all always reads/);
});

test('a plaintext hook with no insecure_ssl field is still the finding', () => {
  const hook = { id: 5, config: { url: 'http://hooks.acme.io/github' } };
  assert.ok(!looksCompliant(hook));
  assert.equal(classify(hook)[0], 'plaintext');
});

test('the private ranges stop where they should', () => {
  assert.ok(isPrivateHost('10.0.0.1'));
  assert.ok(isPrivateHost('192.168.1.7'));
  assert.ok(isPrivateHost('172.16.0.1'));
  assert.ok(isPrivateHost('172.31.255.254'));
  assert.ok(isPrivateHost('127.0.0.1'));
  assert.ok(isPrivateHost('169.254.169.254'));
  assert.ok(!isPrivateHost('172.15.0.1'));
  assert.ok(!isPrivateHost('172.32.0.1'));
  assert.ok(!isPrivateHost('8.8.8.8'));
  assert.ok(!isPrivateHost('hooks.acme.io'));
});

test('local names and ipv6 loopback count as unreachable', () => {
  assert.ok(isPrivateHost('localhost'));
  assert.ok(isPrivateHost('build-01.internal'));
  assert.ok(isPrivateHost('printer.local'));
  assert.ok(isPrivateHost('::1'));
  assert.ok(isPrivateHost('fd00::1'));
  assert.ok(!isPrivateHost(''));
  assert.ok(!isPrivateHost(null));
});

test('the printed url survives a query string and a userinfo prefix', () => {
  assert.equal(safeUrl('http://hooks.acme.io/github?token=abc123'),
    'http://hooks.acme.io/github');
  assert.equal(safeUrl('https://bot:hunter2@hooks.acme.io/x'),
    'https://<redacted>@hooks.acme.io/x');
  assert.ok(!safeUrl('https://bot:hunter2@hooks.acme.io/x').includes('hunter2'));
  assert.equal(safeUrl(''), '');
});

test('the host is parsed out of the shapes a url arrives in', () => {
  assert.equal(hostOf('http://hooks.acme.io:8080/github'), 'hooks.acme.io');
  assert.equal(hostOf('http://bot:pw@10.0.0.4/hooks'), '10.0.0.4');
  assert.equal(hostOf('http://[::1]:3000/hooks'), '::1');
  assert.equal(schemeOf('HTTP://hooks.acme.io'), 'http');
  assert.equal(schemeOf('hooks.acme.io'), '');
});

test('a hook with no url is not counted either way', () => {
  assert.equal(classify({ id: 6, config: {} })[0], 'no-scheme');
  assert.equal(classify({ id: 7, config: { url: 'ftp://x.example/h' } })[0],
    'unknown-scheme');
});

test('the summary separates leaking from unreachable', () => {
  assert.deepEqual(summarize([OPEN, LOCAL, TLS, UNVERIFIED]), {
    total: 4, plaintext: 1, unreachable: 1, encrypted: 2, unreadable: 0,
  });
});
