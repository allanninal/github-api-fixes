/**
 * Compute the request rate one endpoint can sustain before it is throttled.
 *
 * Read only. Every request is a GET, and the default sampled path is
 * /rate_limit, which does not count against the primary rate limit.
 *
 * Two per-minute ceilings apply to a single endpoint: 900 points, and 90
 * seconds of CPU per 60 seconds of real time. Response time is the documented
 * rough estimate of the second, so timed GETs are enough to see which binds.
 */
const API = 'https://api.github.com';
const UA = 'github-endpoint-cost-audit/1.0';

// Documented secondary limits for a single REST endpoint.
export const POINT_CAP = 900;
export const CPU_CAP = 90;

// Reads cost one point; everything that changes state costs five.
const CHEAP_METHODS = ['GET', 'HEAD', 'OPTIONS'];

/**
 * Documented point cost of one request. Pure.
 * Unrecognised methods are charged the expensive rate, because guessing low
 * produces a safe-looking ceiling for a request that is not safe.
 */
export function pointsFor(method) {
  const name = String(method ?? '').trim().toUpperCase();
  return CHEAP_METHODS.includes(name) ? 1 : 5;
}

/**
 * Collapse timed samples into one entry per path. Pure.
 * Keeps the max as well as the mean: a path with a comfortable mean and a bad
 * worst case is throttled during the worst case and nowhere else.
 */
export function costProfile(samples) {
  const grouped = new Map();
  for (const s of samples ?? []) {
    const path = s?.path === undefined ? null : String(s.path);
    const seconds = Number(s?.seconds);
    if (path === null || !Number.isFinite(seconds) || seconds < 0) continue;
    if (!grouped.has(path)) {
      grouped.set(path, {
        path, calls: 0, total: 0, max_seconds: 0, points: pointsFor(s.method ?? 'GET'),
      });
    }
    const entry = grouped.get(path);
    entry.calls += 1;
    entry.total += seconds;
    entry.max_seconds = Math.max(entry.max_seconds, seconds);
  }

  const out = {};
  for (const [path, entry] of grouped) {
    entry.mean_seconds = Math.round((entry.total / entry.calls) * 10000) / 10000;
    entry.max_seconds = Math.round(entry.max_seconds * 10000) / 10000;
    delete entry.total;
    out[path] = entry;
  }
  return out;
}

/**
 * Requests per minute this endpoint sustains, and which cap binds. Pure.
 * The CPU ceiling falls as the endpoint gets slower and crosses under the
 * point ceiling at around a tenth of a second a call.
 */
export function safeRate(meanSeconds, points = 1, pointCap = POINT_CAP, cpuCap = CPU_CAP) {
  const secs = Number.isFinite(Number(meanSeconds)) ? Number(meanSeconds) : 0;
  const p = Math.max(1, Number.parseInt(points, 10) || 1);

  const byPoints = pointCap / p;
  const byCpu = secs > 0 ? cpuCap / secs : Infinity;

  const binding = byCpu < byPoints ? 'cpu' : 'points';
  const perMinute = Math.min(byCpu, byPoints);

  return {
    by_points: Math.round(byPoints * 10) / 10,
    by_cpu: Number.isFinite(byCpu) ? Math.round(byCpu * 10) / 10 : null,
    binding,
    per_minute: Math.round(perMinute * 10) / 10,
    mean_seconds: Math.round(secs * 10000) / 10000,
    points: p,
  };
}

/** Compare the computed ceiling against the rate you run at. Pure. */
export function verdict(path, entry, safe, configured = null) {
  const mean = safe.mean_seconds;
  const ceiling = safe.per_minute;
  const capName = safe.binding === 'cpu'
    ? 'the 90s-of-CPU-per-60s cap'
    : 'the 900-points-a-minute cap';

  if (configured === null || configured === undefined) {
    return ['ceiling',
      `${path} costs ${mean.toFixed(3)} s a call, so ${capName} allows about ` +
      `${Math.trunc(ceiling)} request(s) a minute on this path.`];
  }

  const rate = Number(configured);
  if (!Number.isFinite(rate)) {
    return ['ceiling',
      `${path} allows about ${Math.trunc(ceiling)} a minute; no configured ` +
      'rate was given to compare it against.'];
  }

  if (rate > ceiling) {
    return ['over-budget',
      `${path} is configured for ${Math.trunc(rate)} a minute against a ` +
      `ceiling of ${Math.trunc(ceiling)}. ${capName} binds first at ` +
      `${mean.toFixed(3)} s a call, so the surplus is refused, retried, and ` +
      'refused again.'];
  }

  if (rate >= ceiling * 0.8) {
    return ['near-budget',
      `${path} runs at ${Math.trunc(rate)} a minute against a ceiling of ` +
      `${Math.trunc(ceiling)}. One slower response, or one worst case of ` +
      `${(entry?.max_seconds ?? mean).toFixed(3)} s, closes that gap.`];
  }

  if (mean >= 1) {
    return ['expensive',
      `${path} costs ${mean.toFixed(3)} s a call, which caps it at ` +
      `${Math.trunc(ceiling)} a minute however little you are asking for ` +
      'today. Treat it as a path to move work off rather than a path to pace.'];
  }

  return ['clear',
    `${path} runs at ${Math.trunc(rate)} a minute against a ceiling of ` +
    `${Math.trunc(ceiling)}, ${capName} binding.`];
}

const sleep = (ms) => new Promise((r) => { setTimeout(r, ms); });

/** Time a few sequential GETs. Sequential on purpose: a sampler that fanned
 * out would measure the limit it is trying to describe. */
async function samplePath(token, path, count, pause) {
  const url = path.startsWith('/') ? API + path : path;
  const samples = [];
  let resource = null;
  let throttled = false;
  for (let i = 0; i < count; i += 1) {
    if (i) await sleep(pause * 1000);
    const start = performance.now();
    let res;
    try {
      res = await fetch(url, {
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: 'application/vnd.github+json',
          'X-GitHub-Api-Version': '2022-11-28',
          'User-Agent': UA,
        },
      });
    } catch (err) {
      console.warn(`${path} sample ${i} failed: ${err.message}`);
      continue;
    }
    const text = await res.text();
    const elapsed = (performance.now() - start) / 1000;
    const lowered = {};
    for (const [k, v] of res.headers.entries()) lowered[k.toLowerCase()] = v;
    resource = resource ?? lowered['x-ratelimit-resource'] ?? null;
    if ((res.status === 403 || res.status === 429)
        && text.toLowerCase().includes('secondary rate limit')) {
      throttled = true;
      console.warn(`${path} was throttled while being measured; retry-after ` +
        `${lowered['retry-after'] ?? 'absent'}`);
      continue;
    }
    if (res.status >= 400) {
      console.warn(`${path} sample ${i} returned ${res.status}`);
      continue;
    }
    samples.push({ path, method: 'GET', seconds: elapsed });
  }
  return { samples, resource, throttled };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN (a read-only token is enough)');
    process.exitCode = 2;
    return;
  }
  const path = process.argv[2] ?? '/rate_limit';
  const count = Math.max(1, Number.parseInt(process.argv[3] ?? '4', 10) || 4);
  const rate = process.argv[4] === undefined ? null : Number(process.argv[4]);

  if (path.replace(/\/$/, '') !== '/rate_limit') {
    console.warn(`measuring a path that does cost quota: ${count} sample(s), ` +
      'one point each');
  }

  const { samples, resource, throttled } = await samplePath(token, path, count, 1);
  if (throttled) {
    console.warn(`${path} tripped a secondary limit during measurement, which ` +
      'is itself the finding: the endpoint is already over budget at the rate ' +
      'this sampler used');
  }

  const profile = costProfile(samples);
  const entries = Object.values(profile).sort((a, b) => b.mean_seconds - a.mean_seconds);
  if (!entries.length) {
    console.error('no successful samples, so there is nothing to cost');
    process.exitCode = 2;
    return;
  }

  const findings = [];
  let worst = 'clear';
  for (const entry of entries) {
    const safe = safeRate(entry.mean_seconds, entry.points);
    const [state, detail] = verdict(entry.path, entry, safe, rate);
    findings.push({ path: entry.path, state, max_seconds: entry.max_seconds,
      billed_to: resource, ...safe });
    console.log(`${state.padEnd(14)} ${detail}`);
    if (resource) console.log(`               billed to the ${resource} bucket`);
    if (['over-budget', 'near-budget', 'expensive'].includes(state) && worst === 'clear') {
      worst = state;
    }
  }

  if (worst !== 'clear') {
    console.log('repair: replace per-item calls on the expensive path with one ' +
      'GraphQL query, which is billed to a different allowance entirely.');
    console.log('repair: raise per_page to 100 on list endpoints so the same ' +
      'data arrives in a third of the calls.');
    console.log('repair: send If-None-Match with the stored etag. A 304 costs ' +
      'the server almost nothing and costs you nothing at all.');
    console.log('repair: where the calls are unavoidable, spread them across ' +
      'the minute rather than bursting, and sleep the whole retry-after before ' +
      'resuming that path.');
  }

  console.log(JSON.stringify({ findings, configured_per_minute: rate }, null, 2));
  process.exitCode = worst === 'over-budget' ? 1 : 0;
}

// Only run when invoked directly, so importing this from the test file does not
// start main() and set an exit code the tests never asked for.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
