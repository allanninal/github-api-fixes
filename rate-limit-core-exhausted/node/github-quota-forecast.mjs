/**
 * Forecast when the core REST bucket empties, from three published numbers.
 *
 * Read only. Every request is a GET, and GET /rate_limit does not count
 * against the primary rate limit, so this never spends what it measures.
 */
const API = 'https://api.github.com';
const UA = 'github-quota-forecast/1.0';

// The core bucket is a fixed one-hour window that refills in full at `reset`.
export const WINDOW = 3600;

/**
 * Average drain since this window opened, and where it lands. Pure.
 * reset - WINDOW is when the window opened, which turns a counter into a rate.
 */
export function windowBurn(used, limit, reset, now, window = WINDOW) {
  const u = Number.parseInt(used, 10);
  const l = Number.parseInt(limit, 10);
  const r = Number(reset);
  const t = Number(now);
  if (!Number.isFinite(u) || !Number.isFinite(l) || !Number.isFinite(r) || !Number.isFinite(t)) {
    return null;
  }
  const usedN = Math.max(0, u);
  const limitN = Math.max(1, l);
  // Clamped: a reset further away than the window means the clocks disagree,
  // and erring towards a high drain is the safe direction.
  const left = Math.min(Math.max(r - t, 0), window);
  const elapsed = Math.max(1, window - left);
  const remaining = Math.max(0, limitN - usedN);

  const perMin = usedN / (elapsed / 60);
  const leftMin = left / 60;
  const projected = usedN + perMin * leftMin;
  const affordable = leftMin > 0 ? remaining / leftMin : remaining;

  let emptyIn;
  if (remaining <= 0) emptyIn = 0;
  else if (perMin <= 0) emptyIn = null;
  else {
    const secs = remaining / (perMin / 60);
    emptyIn = secs > left ? null : secs;
  }

  return {
    used: usedN, limit: limitN, remaining,
    elapsed: Math.round(elapsed * 10) / 10,
    left: Math.round(left * 10) / 10,
    per_min: Math.round(perMin * 100) / 100,
    affordable: Math.round(affordable * 100) / 100,
    projected: Math.round(projected),
    empty_in: emptyIn,
  };
}

/**
 * Drain between two samples of the same bucket. Pure.
 * A window that rolled resets `used`, so the difference goes negative. That is
 * a refill, not a negative rate, and it is reported as one.
 */
export function sampleBurn(first, second) {
  if (!first || !second) return ['single', null];
  const u1 = Number.parseInt(first.used, 10);
  const u2 = Number.parseInt(second.used, 10);
  const r1 = Number(first.reset);
  const r2 = Number(second.reset);
  const t1 = Number(first.at);
  const t2 = Number(second.at);
  if (![u1, u2, r1, r2, t1, t2].every(Number.isFinite)) return ['single', null];

  const gap = t2 - t1;
  if (gap <= 0) return ['no-gap', null];
  if (r2 !== r1 || u2 < u1) return ['rolled', null];
  return ['measured', Math.round(((u2 - u1) / (gap / 60)) * 100) / 100];
}

/**
 * Turn the arithmetic into one finding. Pure.
 * Prefers the measured drain: the average is a claim about the past.
 */
export function verdict(win, instant = ['single', null], tight = 0.8) {
  if (!win) return ['unreadable', 'the rate-limit body did not contain usable numbers'];

  const [state, measured] = instant;
  const drain = (state === 'measured' && measured !== null) ? measured : win.per_min;
  const source = state === 'measured'
    ? 'measured over the sample gap'
    : 'averaged over the window so far';
  const mins = win.left / 60;

  if (win.remaining <= 0) {
    return ['exhausted',
      `0 of ${win.limit} left. Every non-search REST call refuses until reset, ` +
      `in ${Math.trunc(win.left)} second(s). Waiting is not the repair, ` +
      'spending less is.'];
  }

  if (drain > win.affordable && drain > 0) {
    const empty = win.remaining / (drain / 60);
    return ['will-exhaust',
      `drain is ${drain.toFixed(1)}/min (${source}) against ` +
      `${win.affordable.toFixed(1)}/min affordable. ${win.remaining} left ` +
      `empties in about ${Math.round(empty / 60)} minute(s), ` +
      `${Math.max(0, Math.round(mins - empty / 60))} minute(s) before reset.`];
  }

  if (state === 'measured' && measured !== null && win.per_min > 0
      && measured > win.per_min * 2) {
    return ['spiky',
      `drain is ${measured.toFixed(1)}/min right now against a ` +
      `${win.per_min.toFixed(1)}/min average for the window. The bucket fits ` +
      'it today, but the average is hiding a burst and a longer burst will not fit.'];
  }

  if (win.used >= win.limit * tight) {
    return ['tight',
      `${win.used} of ${win.limit} used with ${Math.round(mins)} minute(s) to ` +
      `reset. The current drain of ${drain.toFixed(1)}/min fits, but there is ` +
      'no room for a second consumer on this token.'];
  }

  return ['clear',
    `drain ${drain.toFixed(1)}/min against ${win.affordable.toFixed(1)}/min ` +
    `affordable, ${win.remaining} left with ${Math.round(mins)} minute(s) to reset.`];
}

async function sample(token) {
  const res = await fetch(`${API}/rate_limit`, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': UA,
    },
  });
  if (res.status !== 200) {
    console.error(`GET /rate_limit returned ${res.status}: ${(await res.text()).slice(0, 200)}`);
    return null;
  }
  const body = await res.json();
  return { resources: body.resources ?? {}, at: Date.now() / 1000 };
}

const bucket = (snapshot, name) => {
  const b = snapshot?.resources?.[name] ?? {};
  return {
    used: b.used ?? 0, limit: b.limit ?? 0,
    reset: b.reset ?? 0, remaining: b.remaining ?? 0,
    at: snapshot?.at ?? 0,
  };
};

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN (a read-only token is enough)');
    process.exitCode = 2;
    return;
  }
  const resource = process.argv[2] ?? 'core';
  const watch = Math.max(0, Number.parseInt(process.argv[3] ?? '0', 10) || 0);

  const first = await sample(token);
  if (!first) { process.exitCode = 2; return; }

  for (const [name, b] of Object.entries(first.resources).sort()) {
    console.log(`bucket ${name.padEnd(22)} ${b.used} / ${b.limit} remaining ${b.remaining}`);
  }

  let second = null;
  if (watch > 0) {
    console.log(`second sample in ${watch} second(s)`);
    await new Promise((r) => { setTimeout(r, watch * 1000); });
    second = await sample(token);
  }

  const b1 = bucket(first, resource);
  const b2 = second ? bucket(second, resource) : null;
  const win = windowBurn(b1.used, b1.limit, b1.reset, first.at);
  const instant = sampleBurn(b1, b2);
  const [state, detail] = verdict(win, instant);

  if (instant[0] === 'rolled') {
    console.log('the window rolled between samples: the bucket refilled, so ' +
      'there is no drain to measure across that gap');
  }
  console.log(`${state}: ${detail}`);

  if (['exhausted', 'will-exhaust', 'tight', 'spiky'].includes(state)) {
    console.log('repair: send If-None-Match with the etag you already got back. ' +
      'A 304 Not Modified does not count against this bucket at all.');
    console.log('repair: replace per-item REST reads with one GraphQL query, ' +
      'which is billed to a separate bucket entirely.');
    console.log('repair: stop polling and subscribe to a webhook, so the change ' +
      'arrives instead of being asked for every thirty seconds.');
    console.log('repair: for a genuinely large workload, authenticate as a ' +
      'GitHub App installation, whose limit scales up to 12,500 an hour.');
  }

  console.log(JSON.stringify({
    resource, state, window: win,
    instant: { state: instant[0], per_min: instant[1] },
  }, null, 2));
  process.exitCode = (state === 'exhausted' || state === 'will-exhaust') ? 1 : 0;
}

// Only run when invoked directly, so importing this from the test file does not
// start main(), fail on the missing token and set a non-zero exit code.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
