/**
 * Measure the clock on the machine that signs GitHub App JWTs.
 *
 * Read only, and the part that matters needs no credential. Every GitHub
 * response carries a Date header, so the reference clock is free: time a
 * request from both ends and the offset falls out with an error bar attached.
 *
 * GET /rate_limit is used for the samples because it answers unauthenticated
 * and does not consume quota.
 *
 * This script does not open your JWT. The question is whether the machine
 * writing iat agrees with the machine reading it.
 *
 * Sign convention: skew is local minus server, so positive means this host is
 * ahead of GitHub, which is the direction that breaks a JWT.
 */
const API = 'https://api.github.com';
const UA = 'github-clock-skew/1.0';

/** The Date header is quantised to a whole second, so every reading carries this floor. */
export const DATE_RESOLUTION = 1.0;

/** Below this the two clocks are close enough that nothing is actionable. */
export const GRACE = 5;

/** GitHub's own documented advice for the signing code. */
export const RECOMMENDED_BACKDATE = 60;

/** A rate computed over a shorter span than this is noise. */
export const MIN_DRIFT_SPAN = 60;

/** Roughly the discipline a working time daemon holds. */
export const FREE_RUNNING_PPM = 100;

/** Parse an RFC 9110 Date header into epoch seconds. Pure. null on anything odd. */
export function parseHttpDate(value) {
  if (!value) return null;
  const ms = Date.parse(String(value));
  return Number.isNaN(ms) ? null : ms / 1000;
}

/**
 * One exchange reduced to an offset with an error bar. Pure.
 * The server read its clock somewhere between sent and received, so the
 * midpoint is the fairest local comparison and half the round trip, plus the
 * one second of quantisation, bounds how wrong it can be.
 */
export function sampleSkew(serverEpoch, sent, received) {
  if (serverEpoch === null || serverEpoch === undefined) return null;
  const roundTrip = Math.max(Number(received) - Number(sent), 0);
  const midpoint = (Number(sent) + Number(received)) / 2;
  const round3 = (n) => Math.round(n * 1000) / 1000;
  return {
    skew: round3(midpoint - Number(serverEpoch)),
    uncertainty: round3(roundTrip / 2 + DATE_RESOLUTION),
    round_trip: round3(roundTrip),
    at: round3(Number(received)),
  };
}

/**
 * The exchange with the shortest round trip. Pure.
 * Not the mean and not the median: the fastest exchange had the least room to
 * be asymmetric, which is the same reason a time daemon prefers it.
 */
export function bestSample(samples) {
  const usable = (samples || []).filter(Boolean);
  if (!usable.length) return null;
  return usable.reduce((a, b) => (b.round_trip < a.round_trip ? b : a));
}

/**
 * Hours of offset when the skew looks like a timezone rather than drift. Pure.
 * Backdating sixty seconds does nothing about five hours, so this is worth
 * naming separately.
 */
export function timezoneSuspect(skew) {
  if (skew === null || skew === undefined) return null;
  const magnitude = Math.abs(Number(skew));
  if (magnitude < 1500) return null;
  const slots = Math.round(magnitude / 1800);
  if (slots === 0) return null;
  if (Math.abs(magnitude - slots * 1800) <= 90) {
    const hours = slots / 2;
    return skew > 0 ? hours : -hours;
  }
  return null;
}

/** How far to backdate iat so this offset cannot reach GitHub's future. Pure. */
export function backdateNeeded(skew, uncertainty) {
  const need = Number(skew) + Number(uncertainty) + GRACE;
  if (need <= RECOMMENDED_BACKDATE) return RECOMMENDED_BACKDATE;
  return Math.ceil(need / 30) * 30;
}

/**
 * Turn one measured offset into a finding. Pure.
 * Direction first: ahead of GitHub breaks iat, behind it burns the lifetime.
 */
export function classify(skew, uncertainty, backdate) {
  if (skew === null || skew === undefined) {
    return ['unmeasurable',
      'no response carried a usable Date header, so there is no reference ' +
      'clock to compare against. Check that something is not stripping ' +
      'response headers in front of this host.'];
  }
  const hours = timezoneSuspect(skew);
  if (hours !== null) {
    return ['timezone-not-drift',
      `the offset is ${hours > 0 ? '+' : ''}${hours.toFixed(1)} hours, which ` +
      'is a timezone conversion rather than a clock fault. Something built ' +
      'the timestamp from a naive local datetime and treated it as UTC. ' +
      'Backdating will not help; the conversion has to be fixed.'];
  }
  if (skew > 0) {
    const margin = Number(backdate) - (Number(skew) + Number(uncertainty));
    if (margin < 0) {
      return ['iat-lands-in-the-future',
        `this host is ${skew.toFixed(1)}s ahead of GitHub and iat is ` +
        `backdated by ${backdate}s, so the claim lands ${(-margin).toFixed(1)}s ` +
        'into GitHub\'s future and the JWT is refused. Backdate by ' +
        `${backdateNeeded(skew, uncertainty)}s and fix the host clock.`];
    }
    if (margin < GRACE) {
      return ['backdate-has-no-headroom',
        `this host is ${skew.toFixed(1)}s ahead of GitHub and the ${backdate}s ` +
        `backdate absorbs it with only ${margin.toFixed(1)}s to spare, which ` +
        'is close enough to fail on a fast network. Backdate by ' +
        `${backdateNeeded(skew, uncertainty)}s.`];
    }
    if (Math.abs(skew) <= Math.max(uncertainty, GRACE)) {
      return ['clock-in-sync',
        `this host and GitHub agree to within the measurement error of ` +
        `${Number(uncertainty).toFixed(1)}s.`];
    }
    return ['drift-absorbed-by-backdate',
      `this host is ${skew.toFixed(1)}s ahead of GitHub, and the ${backdate}s ` +
      `backdate covers it with ${margin.toFixed(1)}s to spare. The JWT is ` +
      'safe; the clock is still wrong and worth fixing.'];
  }
  if (Math.abs(skew) <= Math.max(uncertainty, GRACE)) {
    return ['clock-in-sync',
      `this host and GitHub agree to within the measurement error of ` +
      `${Number(uncertainty).toFixed(1)}s.`];
  }
  return ['clock-behind-github',
    `this host is ${(-skew).toFixed(1)}s behind GitHub. iat is safe, but ` +
    `every JWT arrives having already spent ${(-skew).toFixed(1)}s of its ` +
    'life, so a short lifetime can expire on the way.'];
}

/**
 * Parts per million of drift between the first and last reading. Pure.
 * readings: [[localTime, skew], ...]. null when the span is too short to
 * support a rate, which is most of the time.
 */
export function driftRate(readings, minSpan = MIN_DRIFT_SPAN) {
  const usable = (readings || []).filter((r) => r && r[1] !== null && r[1] !== undefined);
  if (usable.length < 2) return null;
  const span = Number(usable[usable.length - 1][0]) - Number(usable[0][0]);
  if (span < minSpan) return null;
  const delta = Number(usable[usable.length - 1][1]) - Number(usable[0][1]);
  return Math.round((delta / span) * 1e6 * 10) / 10;
}

/** Say whether the offset is standing still or growing. Pure. */
export function classifyRate(ppm) {
  if (ppm === null || ppm === undefined) {
    return ['rate-not-measurable',
      `the samples do not span ${MIN_DRIFT_SPAN}s, which is the least this ` +
      'measurement can support. Re-run with a longer interval if you want a ' +
      'rate rather than an offset.'];
  }
  if (Math.abs(ppm) <= FREE_RUNNING_PPM) {
    return ['offset-is-static',
      `the offset is holding at ${ppm.toFixed(1)} ppm, so the clock is ` +
      'disciplined and was simply set wrong once.'];
  }
  return ['clock-is-running-free',
    `the offset is moving at ${ppm.toFixed(1)} ppm, which is about ` +
    `${(ppm * 0.0864).toFixed(1)} seconds a day. Nothing is disciplining this ` +
    'clock, so setting it by hand buys only a few days.'];
}

/** Map a confirming GET /app response to the defect it names. Pure. */
export function interpret(status, message) {
  if (status === 200) return ['accepted', 'GitHub did not complain about iat.'];
  const text = String(message ?? '').toLowerCase();
  if (text.includes('issued at') || text.includes("'iat'")) {
    return ['github-refused-iat',
      'GitHub says iat is not a time that has happened, which is this host ' +
      'being ahead of it.'];
  }
  if (text.includes('too far in the future')) {
    return ['lifetime-not-drift',
      'GitHub is complaining about exp rather than iat, so the requested ' +
      'lifetime is over the ceiling and the clock is not the problem.'];
  }
  if (text.includes('could not be decoded')) {
    return ['key-or-encoding',
      'GitHub could not decode the JWT at all, which is a signing key or ' +
      'encoding fault rather than a clock one.'];
  }
  if (text.includes('integration not found')) {
    return ['issuer-does-not-resolve',
      'the iss claim does not name an App GitHub can find, which is a key ' +
      'and issuer problem rather than a clock one.'];
  }
  return ['unrelated',
    'the response does not mention a claim, so this failure has another cause.'];
}

const wait = (seconds) => new Promise((r) => setTimeout(r, seconds * 1000));

async function takeSamples(count, interval) {
  const out = [];
  for (let i = 0; i < count; i += 1) {
    if (i) await wait(interval);
    const sent = Date.now() / 1000;
    let res;
    try {
      res = await fetch(`${API}/rate_limit`, {
        headers: {
          Accept: 'application/vnd.github+json',
          'X-GitHub-Api-Version': '2022-11-28',
          'User-Agent': UA,
        },
      });
    } catch (err) {
      console.error(`sample ${i + 1} failed: ${err.message}`);
      continue;
    }
    const received = Date.now() / 1000;
    const served = parseHttpDate(res.headers.get('date'));
    if (served === null) {
      console.error(`sample ${i + 1} carried no usable Date header`);
      continue;
    }
    out.push(sampleSkew(served, sent, received));
  }
  return out;
}

function flag(name, fallback) {
  const at = process.argv.indexOf(name);
  if (at === -1 || at === process.argv.length - 1) return fallback;
  const value = Number(process.argv[at + 1]);
  return Number.isFinite(value) ? value : fallback;
}

async function main() {
  const count = Math.max(flag('--samples', 3), 1);
  const interval = Math.max(flag('--interval', 2), 0);
  const backdate = flag('--backdate', 0);
  const samples = await takeSamples(count, interval);
  const best = bestSample(samples);
  if (!best) {
    console.error('no sample produced a reading; nothing can be said about this clock');
    process.exitCode = 2;
    return;
  }

  console.log(`best of ${samples.length} sample(s): skew=${best.skew >= 0 ? '+' : ''}` +
    `${best.skew.toFixed(1)}s uncertainty=${best.uncertainty.toFixed(1)}s ` +
    `round_trip=${best.round_trip.toFixed(2)}s`);

  const [state, detail] = classify(best.skew, best.uncertainty, backdate);
  console.log(`${state}: ${detail}`);

  const ppm = driftRate(samples.filter(Boolean).map((s) => [s.at, s.skew]));
  const [rateState, rateDetail] = classifyRate(ppm);
  console.log(`${rateState}: ${rateDetail}`);

  if (process.argv.includes('--confirm')) {
    const jwt = (process.env.GITHUB_APP_JWT || "dummy-github-app-jwt");
    if (!jwt) {
      console.error('--confirm needs GITHUB_APP_JWT set to the JWT your own ' +
        'signing code produces');
    } else {
      // The JWT is sent and nothing else. Never decoded, stored or logged.
      const res = await fetch(`${API}/app`, {
        headers: {
          Authorization: `Bearer ${jwt}`,
          Accept: 'application/vnd.github+json',
          'X-GitHub-Api-Version': '2022-11-28',
          'User-Agent': UA,
        },
      });
      let body = null;
      try { body = await res.json(); } catch { body = null; }
      const message = body && typeof body === 'object' ? body.message : null;
      console.log(`GET /app returned ${res.status}`);
      const [liveState, liveDetail] = interpret(res.status, message);
      console.log(`${liveState}: ${liveDetail}`);
    }
  }

  if (state === 'iat-lands-in-the-future' || state === 'backdate-has-no-headroom') {
    console.log(`repair: set iat to now minus ` +
      `${backdateNeeded(best.skew, best.uncertainty)}s when minting, then ` +
      'install time sync on this host so the offset stops moving');
  }

  console.log(JSON.stringify({
    skew_seconds: best.skew,
    uncertainty_seconds: best.uncertainty,
    round_trip_seconds: best.round_trip,
    samples: samples.length,
    backdate_seconds: backdate,
    drift_ppm: ppm,
    state,
  }, null, 2));
  process.exitCode = (state === 'clock-in-sync' || state === 'drift-absorbed-by-backdate') ? 0 : 1;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main() and set an exit code that fails a passing suite.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
