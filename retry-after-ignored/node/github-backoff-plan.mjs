/**
 * Compute the wait a throttled GitHub response asks for, and cost your retries.
 *
 * Read only. The single live request is a GET against /rate_limit, which does
 * not count against the primary rate limit. Everything that decides the wait is
 * a pure function of the response headers.
 */
const API = 'https://api.github.com';
const UA = 'github-backoff-plan/1.0';

// Where a secondary limit sends no retry-after, the documented advice is to
// wait at least a minute before trying again.
export const SECONDARY_FLOOR_SECONDS = 60;

/**
 * Parse a retry-after header into seconds from now, or null. Pure.
 * HTTP allows either a delay in seconds or an HTTP-date, and a parser that only
 * handles the integer form treats the other as absent.
 */
export function retryAfterSeconds(value, now) {
  const text = String(value ?? '').trim();
  if (!text) return null;
  if (/^\d+$/.test(text)) return Math.max(0, Number.parseInt(text, 10));
  const ms = Date.parse(text);
  if (!Number.isFinite(ms)) return null;
  return Math.max(0, ms / 1000 - Number(now));
}

/**
 * How long a correct client sleeps before its next request. Pure.
 * Returns [seconds, source, detail]. retry-after wins over the reset timestamp
 * because a secondary limit can fire while the primary bucket is untouched.
 */
export function requiredWait(status, headers, now) {
  const lowered = {};
  for (const [k, v] of Object.entries(headers ?? {})) lowered[k.toLowerCase()] = v;
  const code = Number.parseInt(status, 10) || 0;

  if (code !== 403 && code !== 429) {
    return [0, 'none',
      `${code} is not a throttled response, so there is nothing to wait for`];
  }

  const seconds = retryAfterSeconds(lowered['retry-after'], now);
  if (seconds !== null) {
    return [seconds, 'retry-after',
      `the response asked for ${Math.round(seconds)} second(s). Sleep exactly ` +
      'that, not a capped or scaled version of it.'];
  }

  const remainingRaw = Number.parseInt(lowered['x-ratelimit-remaining'], 10);
  const remaining = Number.isFinite(remainingRaw) ? remainingRaw : null;
  const resetRaw = Number.parseFloat(lowered['x-ratelimit-reset']);
  const reset = Number.isFinite(resetRaw) ? resetRaw : null;

  if (remaining === 0 && reset !== null) {
    const wait = Math.max(0, reset - Number(now));
    return [wait, 'x-ratelimit-reset',
      'the hourly quota is spent and returns at the reset timestamp, ' +
      `${Math.round(wait)} second(s) from now`];
  }

  return [SECONDARY_FLOOR_SECONDS, 'floor',
    'no retry-after and the primary bucket is not empty, so this is a secondary ' +
    `limit that sent no wait. Treat ${SECONDARY_FLOOR_SECONDS} seconds as the ` +
    'floor and back off exponentially from there.'];
}

/**
 * Exponential delay for a given attempt number. Pure, and unjittered.
 * Jitter belongs to the caller so this schedule stays predictable.
 */
export function backoff(attempt, base = 1, cap = 60) {
  const n = Math.max(0, Math.trunc(attempt));
  return Math.min(cap, base * (2 ** n));
}

/**
 * How many refused requests a fixed-interval retrier fits in the wait. Pure.
 * Every one of these is sent into a limit that is already engaged.
 */
export function wastedRequests(seconds, interval) {
  const wait = Math.max(0, Number(seconds));
  const gap = Number(interval);
  if (!(gap > 0)) return 0;
  return Math.floor(wait / gap);
}

/** Turn a throttled response into a finding. Pure. Returns [state, report]. */
export function plan(status, headers, now, interval = 1) {
  const [seconds, source, detail] = requiredWait(status, headers, now);
  const wasted = wastedRequests(seconds, interval);
  const report = {
    wait_seconds: Math.round(seconds * 10) / 10,
    source,
    detail,
    wasted_requests: wasted,
    retry_interval: interval,
    fallback_schedule: [0, 1, 2, 3, 4].map((i) => backoff(i)),
  };
  if (source === 'none') return ['not-throttled', report];
  if (wasted >= 60) return ['hammering', report];
  if (wasted > 0) return ['impatient', report];
  return ['honoured', report];
}

function parseHeader(text) {
  const at = String(text).indexOf(':');
  if (at < 0) return [String(text).trim(), ''];
  return [String(text).slice(0, at).trim(), String(text).slice(at + 1).trim()];
}

async function main() {
  const args = process.argv.slice(2);
  const now = Date.now() / 1000;

  let status = null;
  let interval = 1;
  const headers = {};
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === '--status') { status = Number.parseInt(args[i + 1], 10); i += 1; }
    else if (args[i] === '--interval') { interval = Number.parseFloat(args[i + 1]); i += 1; }
    else if (args[i] === '--header') {
      const [name, value] = parseHeader(args[i + 1]);
      headers[name] = value;
      i += 1;
    }
  }

  let live = headers;
  if (status === null) {
    const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
    if (!token) {
      console.error('set GITHUB_TOKEN (a read-only token is enough), or pass ' +
        '--status and --header to analyse a captured response');
      process.exitCode = 2;
      return;
    }
    const res = await fetch(`${API}/rate_limit`, {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': UA,
      },
    });
    status = res.status;
    live = Object.fromEntries(res.headers.entries());
    console.log(`probed GET /rate_limit: ${status} (this endpoint does not consume quota)`);
  } else {
    console.log(`analysing a captured ${status} with ${Object.keys(headers).length} header(s)`);
  }

  const [state, report] = plan(status, live, now, interval);
  console.log(`${state}: wait ${Math.round(report.wait_seconds)}s from ${report.source}`);
  console.log(`  ${report.detail}`);

  if (state === 'not-throttled') {
    console.log('  nothing is throttled right now. Re-run with --status and ' +
      '--header against a response captured during an incident to cost your ' +
      'current retry policy.');
    return;
  }

  console.warn(`  a ${report.retry_interval}s retry interval sends ` +
    `${report.wasted_requests} refused request(s) inside that window`);
  console.warn(`  repair: sleep the whole client for ${Math.round(report.wait_seconds)} ` +
    'second(s) before the next request, not one call.');
  console.warn('  repair: branch on retry-after first, then on ' +
    'x-ratelimit-remaining being 0 plus x-ratelimit-reset, and only then on a ' +
    `jittered exponential schedule such as ${report.fallback_schedule.join('s, ')}s`);
  process.exitCode = 1;
}

// Only run when invoked directly. The test file imports this module, and without
// the guard main() would run there too, fail on the missing token, and set a
// non-zero exit code that fails the whole test file even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
