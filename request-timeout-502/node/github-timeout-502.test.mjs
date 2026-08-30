import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  GATEWAY, classify, isGateway, isThrottled, lowerHeaders, narrow,
  narrowingExhausted, nearCutoff, parseParams, readCost, repair, requestId,
  retryRepeatsIt, wastedRetries,
} from './github-timeout-502.mjs';

const THROTTLE = { 'Retry-After': '60' };
const EXHAUSTED = { 'X-RateLimit-Remaining': '0' };
const RID = { 'X-GitHub-Request-Id': 'C4E2:1F03:9AB' };

test('only gateway-shaped statuses count', () => {
  assert.ok(isGateway(502));
  assert.ok(isGateway(504));
  assert.ok(!GATEWAY.includes(500));
  assert.ok(!isGateway(500));
  assert.ok(!isGateway(200));
  assert.ok(!isGateway(null));
});

test('headers are read case-insensitively', () => {
  assert.equal(lowerHeaders(RID)['x-github-request-id'], 'C4E2:1F03:9AB');
  assert.equal(requestId(RID), 'C4E2:1F03:9AB');
  assert.equal(requestId({}), null);
  assert.equal(requestId(null), null);
});

test('a throttle is recognised before anything else', () => {
  assert.ok(isThrottled(403, THROTTLE));
  assert.ok(isThrottled(429, EXHAUSTED));
  assert.ok(!isThrottled(403, {}));
  assert.ok(!isThrottled(502, THROTTLE));
  assert.equal(classify(403, 0.4, THROTTLE)[0], 'throttled');
  assert.equal(classify(429, 0.2, EXHAUSTED)[0], 'throttled');
});

test('the cutoff has a tolerance and it is generous', () => {
  assert.ok(nearCutoff(10.4));
  assert.ok(nearCutoff(8.0));
  assert.ok(!nearCutoff(7.9));
  assert.ok(!nearCutoff(0.3));
  assert.ok(!nearCutoff(null));
});

test('a gateway error at the cutoff is the finding', () => {
  const [state, detail] = classify(502, 10.4, RID);
  assert.equal(state, 'timeout');
  assert.match(detail, /10\.4s/);
  assert.match(detail, /too expensive/);
});

test('the same status arriving fast is a different diagnosis', () => {
  const [state, detail] = classify(502, 0.3, {});
  assert.equal(state, 'gateway-early');
  assert.match(detail, /status page/);
});

test('a success just under the line is not a pass', () => {
  const [state, detail] = classify(200, 9.4, {});
  assert.equal(state, 'slow-success');
  assert.match(detail, /fails on the week/);
  assert.equal(classify(200, 0.4, {})[0], 'ok');
});

test('the other failures are named rather than lumped in', () => {
  assert.equal(classify(500, 3.0, {})[0], 'server-other');
  assert.equal(classify(404, 0.2, {})[0], 'client-error');
  assert.equal(classify(null, 30.0, {})[0], 'client-timeout');
  assert.equal(classify(null, null, {})[0], 'unknown');
  assert.equal(classify('not a status', 1.0, {})[0], 'unknown');
});

test('only the states a retry cannot fix are called repeatable', () => {
  assert.ok(retryRepeatsIt('timeout'));
  assert.ok(retryRepeatsIt('client-timeout'));
  assert.ok(!retryRepeatsIt('gateway-early'));
  assert.ok(!retryRepeatsIt('throttled'));
  assert.equal(wastedRetries('timeout', 3), 3);
  assert.equal(wastedRetries('gateway-early', 3), 0);
  assert.equal(wastedRetries('timeout', null), 0);
});

test('narrowing halves the page and keeps everything else', () => {
  assert.equal(narrow({ per_page: 100 }).per_page, 50);
  assert.equal(narrow({}).per_page, 50);
  assert.equal(narrow({ per_page: 1 }).per_page, 1);
  assert.equal(narrow({ per_page: 40, since: '2026-01-01' }).since, '2026-01-01');
  assert.ok(!narrowingExhausted({ per_page: 2 }));
  assert.ok(narrowingExhausted({ per_page: 1 }));
  assert.ok(!narrowingExhausted({}));
});

test('the repair for a timeout never says retry', () => {
  const text = repair('timeout', { per_page: 100 });
  assert.match(text, /cheaper/);
  assert.match(text, /x-github-request-id/);
  assert.match(repair('timeout', { per_page: 1 }), /split by range/);
  assert.match(repair('throttled'), /wait exactly as long/);
  assert.match(repair('gateway-early'), /status page/);
  assert.equal(repair('ok'), 'nothing.');
});

test('the baseline is never counted as spending', () => {
  assert.equal(readCost(['/a'], 2), 2);
  assert.equal(readCost(['/a', '/b'], 3), 6);
  assert.equal(readCost(['/a'], 0), 0);
  assert.equal(readCost([], 2), 0);
  assert.equal(readCost(null, 2), 0);
});

test('parameters survive a value containing an equals sign', () => {
  assert.deepEqual(parseParams(['per_page=100', 'q=repo:acme/x is:open']),
    { per_page: '100', q: 'repo:acme/x is:open' });
  assert.equal(parseParams(['base=v1...main']).base, 'v1...main');
  assert.deepEqual(parseParams(null), {});
});
