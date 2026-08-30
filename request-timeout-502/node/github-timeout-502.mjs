/**
 * Tell an expensive request that GitHub gave up on from an incident.
 *
 * Read only. Two timed GETs against the path under test, plus one free
 * baseline against GET /rate_limit. Nothing is written and the repair is
 * printed rather than performed.
 *
 * Environment:
 *   GITHUB_TOKEN     a token with read access to the repository
 *   GITHUB_PATH      the expensive API path, e.g. /repos/o/n/compare/v1...main
 *   GITHUB_PARAMS    key=value pairs separated by commas, optional
 *   GITHUB_ATTEMPTS  timed attempts, default 2
 *   GITHUB_TIMEOUT   client timeout in seconds, default 30
 */
const API = 'https://api.github.com';
const UA = 'github-timeout-502/1.0';

/** The server-side budget for a single request, in seconds. */
export const CUTOFF_SECONDS = 10.0;
/** How close to the cutoff still counts as having run out of time. */
export const TOLERANCE = 2.0;
/** The statuses a killed request comes back as. 500 is deliberately not here. */
export const GATEWAY = [502, 503, 504];
export const MAX_PER_PAGE = 100;

/** A finite number, or null. Pure. Number(null) is 0, which would lie here. */
function toNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** Headers keyed by lowercase name. Pure. */
export function lowerHeaders(headers) {
  const out = {};
  for (const [k, v] of Object.entries(headers || {})) out[String(k).toLowerCase()] = v;
  return out;
}

/** The value support will ask for, or null. Pure. */
export function requestId(headers) {
  const h = lowerHeaders(headers);
  return Object.prototype.hasOwnProperty.call(h, 'x-github-request-id')
    ? h['x-github-request-id'] : null;
}

/** Whether this status is the shape a killed request comes back as. Pure. */
export function isGateway(status) {
  const n = toNumber(status);
  return n !== null && GATEWAY.includes(n);
}

/** Whether the response is a rate limit rather than a timeout. Pure. */
export function isThrottled(status, headers) {
  const h = lowerHeaders(headers);
  const code = toNumber(status);
  if (code === null || ![403, 429].includes(code)) return false;
  if (Object.prototype.hasOwnProperty.call(h, 'retry-after')) return true;
  return String(h['x-ratelimit-remaining'] ?? '').trim() === '0';
}

/** Whether this call ran long enough to have been killed for it. Pure. */
export function nearCutoff(elapsed, cutoff = CUTOFF_SECONDS, tolerance = TOLERANCE) {
  const secs = toNumber(elapsed);
  return secs !== null && secs >= cutoff - tolerance;
}

/** Classify one timed attempt. Pure. Returns [state, detail]. */
export function classify(status, elapsed, headers = null) {
  const secs = toNumber(elapsed);

  if (status === null || status === undefined) {
    if (secs !== null && secs >= CUTOFF_SECONDS) {
      return ['client-timeout',
        `your own client gave up after ${secs.toFixed(1)}s, which is at or past `
        + "the server's own budget, so there is no response to read."];
    }
    return ['unknown', 'the attempt produced neither a status nor a usable elapsed time.'];
  }

  const code = toNumber(status);
  if (code === null) return ['unknown', 'the attempt produced no readable status.'];

  if (isThrottled(code, headers)) {
    return ['throttled',
      `${code} carries rate-limit headers, so this is a throttle and not a `
      + 'timeout. The response says how long to wait and waiting is the repair.'];
  }

  if (isGateway(code)) {
    if (secs !== null && nearCutoff(secs)) {
      return ['timeout',
        `${code} came back after ${secs.toFixed(1)}s, at the cutoff GitHub `
        + 'applies to a single request. The query is too expensive to serve, '
        + 'not unlucky.'];
    }
    return ['gateway-early',
      `${code} came back after ${(secs === null ? -1 : secs).toFixed(1)}s, far `
      + 'short of the cutoff, so this is not your query running out of time. '
      + 'Check the status page before rewriting anything.'];
  }

  if (code >= 500 && code < 600) {
    return ['server-other',
      `${code} is a server error of a different shape. It is not the `
      + 'per-request cutoff and it is not a throttle.'];
  }

  if (code >= 400 && code < 500) {
    return ['client-error',
      `${code} is a client error, so the request was understood and refused `
      + 'rather than abandoned partway through.'];
  }

  if (secs !== null && nearCutoff(secs)) {
    return ['slow-success',
      `the call answered ${code} in ${secs.toFixed(1)}s, inside the tolerance `
      + `of the ${CUTOFF_SECONDS.toFixed(0)}s cutoff. It works today and fails `
      + 'on the week the repository grows.'];
  }

  return ['ok',
    `the call answered ${code} in ${(secs === null ? -1 : secs).toFixed(1)}s, `
    + 'comfortably inside the cutoff.'];
}

/** Whether sending the identical request again reproduces this. Pure. */
export function retryRepeatsIt(state) {
  return state === 'timeout' || state === 'client-timeout';
}

/** Attempts a retry wrapper would spend to no purpose at all. Pure. */
export function wastedRetries(state, retries) {
  const n = toNumber(retries);
  if (n === null) return 0;
  return retryRepeatsIt(state) ? Math.max(0, Math.trunc(n)) : 0;
}

/** A cheaper version of the same request. Pure. */
export function narrow(params) {
  const out = { ...(params || {}) };
  const size = toNumber(out.per_page) ?? MAX_PER_PAGE;
  out.per_page = Math.max(1, Math.trunc(size / 2));
  return out;
}

/** Whether the page size can no longer be halved. Pure. */
export function narrowingExhausted(params) {
  const size = toNumber((params || {}).per_page);
  return size === null ? false : size <= 1;
}

/** The sentence a reader has to act on. Pure. */
export function repair(state, params = null) {
  if (state === 'timeout') {
    const base = 'make the request cheaper rather than sending it again: halve '
      + 'per_page, add a date or path filter, split a comparison into ranges, or '
      + 'ask GraphQL for only the fields you need. Record x-github-request-id '
      + 'from the failing response first, because the retry destroys it.';
    if (narrowingExhausted(params)) {
      return `${base} The page size is already at 1, so the request has to be `
        + 'split by range or path instead.';
    }
    return base;
  }
  if (state === 'client-timeout') {
    return "raise your own client timeout above the server's budget and run this "
      + 'again. Until you wait longer than GitHub does you are diagnosing your '
      + "own deadline, not GitHub's.";
  }
  if (state === 'gateway-early') {
    return 'retry this one and check the status page. A gateway error that '
      + 'arrives in a fraction of a second is not your query running out of time.';
  }
  if (state === 'throttled') {
    return 'wait exactly as long as the response tells you to. This is the '
      + 'rate-limit path, it has its own repair, and rewriting the query will '
      + 'not change it.';
  }
  if (state === 'slow-success') {
    return 'narrow it now, while it still works. A call this close to the cutoff '
      + 'crosses it on the busiest day of the quarter.';
  }
  if (state === 'server-other') {
    return 'retry once, then take x-github-request-id to support. This is '
      + 'neither the per-request cutoff nor a throttle.';
  }
  if (state === 'client-error') {
    return 'read the status: the request was refused, not abandoned.';
  }
  if (state === 'ok') return 'nothing.';
  return 'give the probe a path it can reach and a timeout longer than 10s.';
}

/** Requests this run will spend against the core quota. Pure. */
export function readCost(paths, attempts = 2) {
  const n = Array.isArray(paths) ? paths.length : 0;
  const tries = toNumber(attempts);
  if (tries === null) return 0;
  return n * Math.max(0, Math.trunc(tries));
}

/** key=value strings into an object. Pure. */
export function parseParams(pairs) {
  const out = {};
  for (const pair of pairs || []) {
    const i = String(pair).indexOf('=');
    if (i > 0) out[String(pair).slice(0, i).trim()] = String(pair).slice(i + 1).trim();
  }
  return out;
}

function headersFor(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
}

async function timedGet(token, path, params, timeoutSeconds) {
  const url = new URL(API + path);
  for (const [k, v] of Object.entries(params || {})) url.searchParams.set(k, String(v));
  const started = process.hrtime.bigint();
  const elapsed = () => Number(process.hrtime.bigint() - started) / 1e9;
  try {
    const res = await fetch(url, {
      headers: headersFor(token),
      signal: AbortSignal.timeout(timeoutSeconds * 1000),
    });
    return { status: res.status, elapsed: elapsed(), headers: Object.fromEntries(res.headers) };
  } catch {
    return { status: null, elapsed: elapsed(), headers: {} };
  }
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const path = (process.env.GITHUB_PATH || "dummy-github-path");
  if (!token || !path) {
    console.error('set GITHUB_TOKEN (read-only is enough) and GITHUB_PATH');
    process.exitCode = 2;
    return;
  }
  const params = parseParams(((process.env.GITHUB_PARAM || "dummy-github-param")S || '').split(',').filter(Boolean));
  const attempts = Number((process.env.GITHUB_ATTEMPT || "dummy-github-attempt")S || 2);
  const timeoutSeconds = Number((process.env.GITHUB_TIMEOU || "dummy-github-timeou")T || 30);
  console.log(`read cost: ${readCost([path], attempts)} request(s) against the core `
    + 'hourly quota (the baseline is free)');

  const base = await timedGet(token, '/rate_limit', {}, timeoutSeconds);
  console.log(`baseline: GET /rate_limit answered in ${base.elapsed.toFixed(2)}s `
    + 'and consumed no quota');

  const tried = [];
  for (let i = 0; i < Math.max(1, attempts); i += 1) {
    const { status, elapsed, headers } = await timedGet(token, path, params, timeoutSeconds);
    const [state, detail] = classify(status, elapsed, headers);
    const rid = requestId(headers);
    console.log(`attempt ${i + 1}: ${status} after ${elapsed.toFixed(1)}s`
      + (rid ? ` (x-github-request-id ${rid})` : ''));
    tried.push({ status, elapsed: Number(elapsed.toFixed(2)), request_id: rid, state, detail });
  }

  const worst = tried.find((a) => retryRepeatsIt(a.state)) || tried[0];
  console.log(`${worst.state}: ${worst.detail}`);
  console.log(`repair: ${repair(worst.state, params)}`);
  if (['timeout', 'slow-success'].includes(worst.state)) {
    console.log(`try instead: ${Object.entries(narrow(params)).sort()
      .map(([k, v]) => `${k}=${v}`).join(', ')}`);
  }

  console.log(JSON.stringify({
    requests_spent: readCost([path], attempts),
    findings: [{
      path,
      baseline_seconds: Number(base.elapsed.toFixed(3)),
      attempts: tried,
      state: worst.state,
      detail: worst.detail,
      retry_reproduces_it: retryRepeatsIt(worst.state),
      retries_wasted_on_three: wastedRetries(worst.state, 3),
      narrowed_params: narrow(params),
      repair: repair(worst.state, params),
    }],
  }, null, 2));
  process.exitCode = ['timeout', 'client-timeout', 'slow-success'].includes(worst.state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
