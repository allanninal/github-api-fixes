import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  contentTypeOf, deliveryState, hostOf, lastDelivery, repair, secretState,
  subscribedEvents, urlClass, verdict,
} from './github-app-hook-config.mjs';

const NOW = new Date('2026-08-31T00:00:00Z');
const EVENTS = ['push', 'pull_request', 'issues', 'release'];
const RECENT = [{ delivered_at: '2026-08-30T10:00:00Z' }, { delivered_at: '2026-08-29T10:00:00Z' }];
const OLD = [{ delivered_at: '2026-01-04T10:00:00Z' }];

test('a host is pulled out of a URL or admitted missing', () => {
  assert.equal(hostOf('https://Hooks.Example.COM/github'), 'hooks.example.com');
  assert.equal(hostOf('nonsense'), '');
  assert.equal(hostOf(null), '');
});

test('the four ways this actually happens are each named', () => {
  assert.equal(urlClass(''), 'unset');
  assert.equal(urlClass(null), 'unset');
  assert.equal(urlClass('https://smee.io/aB3xQ9pLm'), 'tunnel');
  assert.equal(urlClass('https://1a2b3c.ngrok-free.app/hook'), 'tunnel');
  assert.equal(urlClass('https://example.com/webhook'), 'placeholder');
  assert.equal(urlClass('https://localhost:3000/hook'), 'loopback');
});

test('a real destination is not swept up by the placeholder list', () => {
  assert.equal(urlClass('https://hooks.acme.dev/github'), 'production');
  assert.equal(urlClass('https://api.example-corp.com/github'), 'production');
});

test('loopback beats transport so the reader goes to the right note', () => {
  assert.equal(urlClass('http://localhost:3000/hook'), 'loopback');
  assert.equal(urlClass('http://hooks.acme.dev/github'), 'insecure');
  assert.equal(urlClass('ftp://hooks.acme.dev/github'), 'malformed');
  assert.equal(urlClass('just-a-string'), 'malformed');
});

test('the config is read without touching the secret', () => {
  assert.equal(secretState({ secret: '********' }), 'set');
  assert.equal(secretState({ url: 'https://x.dev' }), 'absent');
  assert.equal(contentTypeOf({}), 'form');
  assert.equal(contentTypeOf({ content_type: 'JSON' }), 'json');
  assert.deepEqual(subscribedEvents({ events: EVENTS }), EVENTS);
  assert.deepEqual(subscribedEvents({}), []);
});

test('a blank URL with subscriptions is the sharpest form', () => {
  const [state, detail] = verdict('', EVENTS, 'none');
  assert.equal(state, 'no-url-subscribed');
  assert.ok(detail.includes('4 event(s)'));
  assert.match(detail, /no log to read/);
});

test('a blank URL with no subscriptions is reported, not judged', () => {
  const [state, detail] = verdict('', [], 'none');
  assert.equal(state, 'no-url');
  assert.match(detail, /reported rather than judged/);
});

test('a tunnel URL is as broken as a blank one and harder to see', () => {
  const [state, detail] = verdict('https://smee.io/aB3xQ9pLm', EVENTS, 'recent');
  assert.equal(state, 'tunnel-url');
  assert.match(detail, /nobody is listening/);
});

test('the delivery log is read and never trusted alone', () => {
  assert.equal(deliveryState([], NOW), 'none');
  assert.equal(deliveryState(RECENT, NOW), 'recent');
  assert.equal(deliveryState(OLD, NOW), 'stale');
  assert.equal(deliveryState(null, NOW), 'unknown');
  assert.equal(lastDelivery(RECENT).getUTCDate(), 30);
  assert.equal(lastDelivery([]), null);
});

test('an empty log on a real URL is a question and not a verdict', () => {
  const [state, detail] = verdict('https://hooks.acme.dev/github', EVENTS, 'none');
  assert.equal(state, 'no-deliveries');
  assert.match(detail, /genuinely not happened/);
  assert.match(repair('no-deliveries'), /not proof of anything/);
});

test('a real URL with no subscriptions is handed to the other note', () => {
  const [state, detail] = verdict('https://hooks.acme.dev/github', [], 'none');
  assert.equal(state, 'no-events');
  assert.match(detail, /subscription finding/);
});

test('a working App is not a finding', () => {
  assert.equal(verdict('https://hooks.acme.dev/github', EVENTS, 'recent')[0], 'delivering');
  assert.equal(verdict('https://hooks.acme.dev/github', EVENTS, 'stale')[0], 'silent');
});

test('the repair ends at the delivery log rather than the settings page', () => {
  assert.ok(repair('tunnel-url').includes('app/hook/deliveries'));
  assert.match(repair('no-url-subscribed'), /settings page/);
  assert.match(repair('insecure-url'), /https/);
});
