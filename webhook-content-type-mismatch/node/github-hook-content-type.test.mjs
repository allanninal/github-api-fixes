import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  contentTypeOf, contentTypeWasExplicit, deliveryEncoding, encodingOfHeader,
  headerOf, isFormWrapped, parseFailures, receiverOf, repair, verdict,
  wrapperEvidence,
} from './github-hook-content-type.mjs';

const FORM_DELIVERY = {
  id: 1,
  status_code: 200,
  request: {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-GitHub-Event': 'push' },
    payload: { payload: '{ "action": "opened" }' },
  },
};
const JSON_DELIVERY = {
  id: 2,
  status_code: 200,
  request: {
    headers: { 'content-type': 'application/json; charset=utf-8' },
    payload: { action: 'opened', number: 7 },
  },
};

test('an absent content_type is form, not unknown', () => {
  assert.equal(contentTypeOf({}), 'form');
  assert.equal(contentTypeOf({ url: 'https://example.com' }), 'form');
  assert.ok(!contentTypeWasExplicit({}));
  assert.ok(contentTypeWasExplicit({ content_type: 'form' }));
});

test('both spellings of each encoding are understood', () => {
  assert.equal(contentTypeOf({ content_type: 'json' }), 'json');
  assert.equal(contentTypeOf({ content_type: 'application/json' }), 'json');
  assert.equal(contentTypeOf({ content_type: ' FORM ' }), 'form');
  assert.equal(contentTypeOf({ content_type: 'application/x-www-form-urlencoded' }), 'form');
  assert.equal(contentTypeOf({ content_type: 'text/xml' }), 'unknown');
  assert.equal(contentTypeOf(null), 'unknown');
});

test('headers are read case insensitively and parameters ignored', () => {
  assert.equal(headerOf({ 'Content-Type': 'application/json' }, 'content-type'), 'application/json');
  assert.equal(headerOf({ 'CONTENT-TYPE': 'x' }, 'Content-Type'), 'x');
  assert.equal(headerOf({}, 'content-type'), null);
  assert.equal(headerOf(null, 'content-type'), null);
  assert.equal(encodingOfHeader('application/json; charset=utf-8'), 'json');
  assert.equal(encodingOfHeader('application/x-www-form-urlencoded'), 'form');
  assert.equal(encodingOfHeader(null), 'unknown');
});

test('the delivery record is read from the request half', () => {
  assert.equal(deliveryEncoding(FORM_DELIVERY), 'form');
  assert.equal(deliveryEncoding(JSON_DELIVERY), 'json');
  assert.equal(deliveryEncoding({ status_code: 200 }), 'unknown');
  assert.equal(deliveryEncoding(null), 'unknown');
});

test('the wrapper is one string key and nothing else', () => {
  assert.ok(isFormWrapped({ payload: '{}' }));
  assert.ok(!isFormWrapped({ payload: { action: 'opened' } }));
  assert.ok(!isFormWrapped({ payload: '{}', extra: 1 }));
  assert.ok(!isFormWrapped({ action: 'opened' }));
  assert.ok(!isFormWrapped(null));
});

test('evidence counts the header and the body separately', () => {
  const ev = wrapperEvidence([FORM_DELIVERY, JSON_DELIVERY, null]);
  assert.deepEqual(ev, { sampled: 2, form_header: 1, form_wrapper: 1 });
});

test('parse statuses are counted but only the three', () => {
  const [hits, total] = parseFailures([{ status_code: 400 }, { status_code: 415 },
    { status_code: 500 }, { status_code: 200 }, { status_code: null }]);
  assert.equal(hits, 2);
  assert.equal(total, 5);
});

test('a form hook against a JSON receiver is the finding', () => {
  const [state, detail] = verdict('form', 'json');
  assert.equal(state, 'form-to-json');
  assert.match(detail, /payload= field/);
});

test('a clean delivery log does not soften the finding', () => {
  const ev = { sampled: 5, form_header: 5, form_wrapper: 5 };
  const [state, detail] = verdict('form', 'json', ev, 0, 40);
  assert.equal(state, 'form-to-json');
  assert.match(detail, /5 of 5 sampled deliveries/);
});

test('parse statuses are reported as corroboration', () => {
  const [state, detail] = verdict('form', 'json', null, 12, 40);
  assert.equal(state, 'form-to-json');
  assert.match(detail, /12 of 40 recent attempts/);
});

test('the mirror case is named rather than folded in', () => {
  assert.equal(verdict('json', 'form')[0], 'json-to-form');
  assert.match(repair('json-to-form'), /wrong direction/);
});

test('an undeclared receiver gives a risk and not a verdict', () => {
  const [state, detail] = verdict('form', null);
  assert.equal(state, 'receiver-undeclared');
  assert.match(detail, /risk rather than a finding/);
});

test('consistent pairs are not findings', () => {
  assert.equal(verdict('json', 'json')[0], 'consistent-json');
  assert.equal(verdict('form', 'form')[0], 'consistent-form');
});

test('a consistent form hook is still warned about the signature', () => {
  assert.match(verdict('form', 'form')[1], /raw bytes/);
});

test('an unrecognised encoding is never guessed at', () => {
  assert.equal(verdict('unknown', 'json')[0], 'encoding-unknown');
  assert.match(repair('encoding-unknown'), /by hand/);
});

test('the receiver flag is normalised defensively', () => {
  assert.equal(receiverOf('JSON'), 'json');
  assert.equal(receiverOf(' form '), 'form');
  assert.equal(receiverOf('maybe'), 'unknown');
  assert.equal(receiverOf(null), 'unknown');
});

test('the repair always pairs the encoding with the verifier', () => {
  assert.match(repair('form-to-json'), /raw request bytes/);
  assert.equal(repair('consistent-json'), 'nothing.');
});
