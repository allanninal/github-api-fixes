/**
 * Measure the concurrency a client actually reaches, and classify any throttling.
 *
 * Read only. Every request is a GET, and the default probe endpoint is
 * GET /rate_limit, which does not count against the primary rate limit.
 *
 * Secondary limits have no headroom API, so nothing here predicts one.
 */
const API = 'https://api.github.com';
const UA = 'github-concurrency-probe/1.0';

// Documented ceiling on requests in flight at once, across REST and GraphQL.
export const CONCURRENCY_CEILING = 100;

// The wording has changed over the years; match the stable part of both forms.
const SECONDARY_MARKERS = ['secondary rate limit', 'abuse detection'];

/**
 * Sort one response into primary, secondary, permission or fine. Pure.
 * The distinguishing field is x-ratelimit-remaining on the refused response:
 * a primary exhaustion reports 0, a secondary limit leaves the bucket alone.
 */
export function classify(status, body, headers) {
  const lowered = {};
  for (const [k, v] of Object.entries(headers ?? {})) lowered[k.toLowerCase()] = v;
  const text = String(body ?? '').toLowerCase();
  const rawRemaining = lowered['x-ratelimit-remaining'];
  const parsed = Number.parseInt(rawRemaining, 10);
  const remaining = Number.isFinite(parsed) ? parsed : null;
  const code = Number.parseInt(status, 10) || 0;

  if (code >= 200 && code < 400) {
    return ['ok', `${code}, primary bucket reports ${remaining ?? 'unknown'} left`];
  }
  if (code !== 403 && code !== 429) return ['other', `${code} is not a throttle at all`];

  if (SECONDARY_MARKERS.some((m) => text.includes(m))) {
    return ['secondary',
      `${code} and the body names a secondary rate limit. The hourly quota is ` +
      `not involved: it still reports ${remaining ?? 'an unknown number'} remaining.`];
  }
  if (remaining === 0) {
    return ['primary',
      `${code} with x-ratelimit-remaining at 0. This is the hourly quota, not a ` +
      'secondary limit, and it clears at x-ratelimit-reset.'];
  }
  if (remaining !== null && remaining > 0) {
    return ['secondary-suspected',
      `${code} while ${remaining} request(s) remain in the primary bucket. The ` +
      'body does not say secondary, but a refusal with headroom left did not ' +
      'come from the bucket these headers describe.'];
  }
  return ['forbidden',
    `${code} with no rate-limit headers to read. Treat this as permissions ` +
    'until something proves otherwise.'];
}

/**
 * Peak number of requests in flight at once, from [start, end] pairs. Pure.
 * A sweep, because the pool size is a ceiling and this is what was reached.
 */
export function peakOverlap(spans) {
  const events = [];
  for (const span of spans ?? []) {
    let start = Number(span[0]);
    let end = Number(span[1]);
    if (end < start) [start, end] = [end, start];
    events.push([start, 1], [end, -1]);
  }
  // Ends sort before starts at an equal timestamp: a request that ended as
  // another began was never beside it.
  events.sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]));
  let peak = 0;
  let current = 0;
  for (const [, delta] of events) {
    current += delta;
    if (current > peak) peak = current;
  }
  return peak;
}

/**
 * Turn a peak overlap and a list of response states into a finding. Pure.
 * "clear" says this run was fine, never that the client is: the limit has no
 * headroom API to check against.
 */
export function verdict(peak, states, ceiling = CONCURRENCY_CEILING) {
  const list = states ?? [];
  const throttled = list.filter((s) => s === 'secondary' || s === 'secondary-suspected');
  if (throttled.length) {
    return ['tripped',
      `${throttled.length} of ${list.length} response(s) were refused with the ` +
      `primary bucket still healthy. Peak overlap was ${peak}. Bound the pool ` +
      'and honour retry-after.'];
  }
  if (peak >= ceiling) {
    return ['over-ceiling',
      `peak overlap ${peak} at or above the documented ceiling of ${ceiling}. ` +
      'This run happened not to be refused; a slower endpoint or a busier ' +
      'moment will be.'];
  }
  if (peak >= ceiling * 0.8) {
    return ['near-ceiling',
      `peak overlap ${peak} against a ceiling of ${ceiling}. One more worker or ` +
      'one slow response is the difference.'];
  }
  return ['clear',
    `peak overlap ${peak} of a ${ceiling} ceiling, nothing throttled. This ` +
    'proves the run was fine, not that the client is: secondary limits have no ' +
    'headroom API to check against.'];
}

async function probe(token, url, index) {
  const start = performance.now() / 1000;
  try {
    const res = await fetch(url, {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': UA,
      },
    });
    const body = (await res.text()).slice(0, 400);
    const headers = Object.fromEntries(res.headers.entries());
    return { i: index, start, end: performance.now() / 1000, status: res.status, body, headers };
  } catch (err) {
    return {
      i: index, start, end: performance.now() / 1000, status: 0,
      body: err.message, headers: {},
    };
  }
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN (a read-only token is enough)');
    process.exitCode = 2;
    return;
  }
  const endpoint = process.argv[2] ?? '/rate_limit';
  const total = Math.max(1, Number.parseInt(process.argv[3] ?? '12', 10) || 12);
  const wanted = Number.parseInt(process.argv[4] ?? '6', 10) || 6;
  const workers = Math.max(1, Math.min(wanted, CONCURRENCY_CEILING));
  const url = endpoint.startsWith('/') ? API + endpoint : endpoint;

  console.log(`probing ${url}: ${total} request(s), pool of ${workers}`);

  // A queue, not Promise.all over the input: the whole point of the note is
  // that Promise.all borrows its concurrency from the length of the list.
  const results = [];
  let next = 0;
  await Promise.all(Array.from({ length: workers }, async () => {
    while (next < total) {
      const index = next;
      next += 1;
      results.push(await probe(token, url, index));
    }
  }));

  const states = [];
  for (const r of results.sort((a, b) => a.i - b.i)) {
    const [state, detail] = classify(r.status, r.body, r.headers);
    states.push(state);
    if (state !== 'ok' && state !== 'other') {
      console.warn(`request ${r.i}: ${state.padEnd(20)} ${detail}`);
      const lowered = {};
      for (const [k, v] of Object.entries(r.headers)) lowered[k.toLowerCase()] = v;
      if (lowered['retry-after']) {
        console.warn(`  retry-after: ${lowered['retry-after']} second(s). Pause ` +
          'the whole pool for that long, not just this request.');
      }
    }
  }

  const peak = peakOverlap(results.map((r) => [r.start, r.end]));
  const [state, detail] = verdict(peak, states);
  console.log(`${state}: ${detail}`);

  if (state !== 'clear') {
    console.log('repair: replace the fan-out with a bounded queue of 6 rather ' +
      'than Promise.all over the whole input list.');
    console.log('repair: on a throttled response sleep retry-after seconds ' +
      'before resuming any worker; where the header is absent wait 60 seconds ' +
      'and then back off exponentially.');
  }

  console.log(JSON.stringify({
    peak_overlap: peak, ceiling: CONCURRENCY_CEILING,
    requests: results.length, state, states,
  }, null, 2));
  process.exitCode = (state === 'tripped' || state === 'over-ceiling') ? 1 : 0;
}

// Only run when invoked directly. The test file imports this module, and without
// the guard main() would run there too, fail on the missing token, and set a
// non-zero exit code that fails the whole test file even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
