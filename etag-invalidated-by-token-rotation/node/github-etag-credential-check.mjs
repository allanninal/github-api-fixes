/**
 * Prove whether a cached ETag survives a change of credential, and cost it.
 *
 * Read only. Three GETs against one URL, and the third is only issued when a
 * second credential is available in the environment.
 *
 * An ETag is scoped to the representation the server produced for that caller,
 * so rotating a credential invalidates the whole cache at once.
 */
import { createHash } from 'node:crypto';

const API = 'https://api.github.com';
const UA = 'github-etag-credential-check/1.0';

// Installation access tokens are valid for one hour.
export const INSTALLATION_TOKEN_TTL = 3600;
export const HOURLY_LIMIT = 5000;

/**
 * Sort the two conditional replays into a finding. Pure.
 * `same` is the control: the etag replayed with the credential that minted it.
 * `other` is the same etag under a second credential.
 */
export function classifyPair(same, other) {
  const code = (v) => {
    const n = Number.parseInt(v, 10);
    return Number.isFinite(n) ? n : null;
  };
  const control = code(same);
  const rotated = code(other);

  if (control === null) {
    return ['inconclusive',
      'the control request did not complete, so nothing below it can be trusted'];
  }
  if (control === 200) {
    return ['not-cacheable',
      'the endpoint answered 200 to its own etag. Either no validator came ' +
      'back, something between here and GitHub stripped the If-None-Match ' +
      'header, or the resource genuinely changed between the two calls. Rule ' +
      'those out before testing rotation.'];
  }
  if (control !== 304) {
    return ['inconclusive',
      `the control request returned ${control} rather than 304 or 200, which ` +
      'is not a cache answer at all'];
  }
  if (rotated === null) {
    return ['unproven',
      'the etag matched its own credential, but no second credential was ' +
      'available to test the rotation against. The projection below is ' +
      'arithmetic, not a measurement.'];
  }
  if (rotated === 304) {
    return ['shared',
      'the same etag matched under both credentials, so rotation is not what ' +
      'is draining this quota. Look for a poll interval or a cache key ' +
      'problem instead.'];
  }
  if (rotated === 200) {
    return ['credential-scoped',
      'the etag that returned 304 for the credential that minted it returned ' +
      '200 for another. Every rotation therefore refetches the entire cache ' +
      'at full price.'];
  }
  return ['inconclusive',
    `the second credential returned ${rotated}, which is neither a match nor a ` +
    'miss. Check that it can read this URL at all.'];
}

/**
 * Full responses per day caused by rotation alone. Pure.
 * The headline is per_rotation, not the daily total: those requests arrive
 * together, which is why this reads as a spike rather than a drift.
 */
export function rotationWaste(urls, pollIntervalS, tokenTtlS,
                              hourlyLimit = HOURLY_LIMIT, hours = 24) {
  const n = Math.max(0, Number.parseInt(urls, 10) || 0);
  const interval = Math.max(1, Number.parseInt(pollIntervalS, 10) || 1);
  const ttl = Math.max(1, Number.parseInt(tokenTtlS, 10) || 1);
  const window = Math.max(0, Number.parseInt(hours, 10) || 0) * 3600;

  const rotations = Math.floor(window / ttl);
  const polls = Math.floor(window / interval) * n;
  return {
    rotations,
    per_rotation: n,
    daily: rotations * n,
    polls,
    hourly_share: Math.round((n / Math.max(1, hourlyLimit)) * 10000) / 10000,
  };
}

/**
 * Seconds left on an installation token from its ISO-8601 expires_at. Pure.
 * null when unreadable, because "already expired" and "could not parse" lead
 * to different next steps.
 */
export function tokenTtl(expiresAt, now) {
  if (!expiresAt) return null;
  const at = Date.parse(String(expiresAt));
  const n = Number(now);
  if (!Number.isFinite(at) || !Number.isFinite(n)) return null;
  return Math.max(0, Math.trunc(at / 1000 - n));
}

/** Combine the measurement and the projection into one finding. Pure. */
export function verdict(state, waste) {
  if (state === 'not-cacheable' || state === 'inconclusive') {
    return [state, 'no rotation cost can be projected until the control request behaves'];
  }
  if (state === 'shared') return ['shared', 'rotation is not the problem here'];

  const share = waste.hourly_share ?? 0;
  const perRotation = waste.per_rotation ?? 0;
  const daily = waste.daily ?? 0;

  if (state === 'unproven' && !daily) {
    return ['clear',
      'nothing to project: no cached urls, or a credential that outlives the window'];
  }
  if (share >= 0.25) {
    return ['rotation-dominates',
      `${perRotation} full response(s) land in the seconds after every mint, ` +
      `which is ${Math.round(share * 100)}% of one hour's entire quota, ` +
      `${waste.rotations ?? 0} time(s) a day`];
  }
  if (daily) {
    return ['rotation-costs',
      `${perRotation} full response(s) per rotation, ${daily} a day, all of ` +
      'which a credential-keyed cache would have kept as 304s'];
  }
  return ['clear', 'the credential outlives the window, so no rotation cost falls inside it'];
}

/** A stable, non-reversible id for a credential, safe to use as a cache key. */
export function fingerprint(token) {
  return createHash('sha256').update(`gh:${token}`).digest('hex').slice(0, 12);
}

async function get(url, token, etag) {
  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
  if (etag) headers['If-None-Match'] = etag;
  const res = await fetch(url, { headers });
  return {
    status: res.status,
    etag: res.headers.get('etag'),
    used: res.headers.get('x-ratelimit-used'),
  };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN (a read-only token is enough)');
    process.exitCode = 2;
    return;
  }
  const second = (process.env.GITHUB_TOKEN_SECOND || "dummy-github-token-second");
  const path = process.argv[2] ?? '/user';
  const urls = Number.parseInt(process.argv[3] ?? '40', 10) || 40;
  const interval = Number.parseInt(process.argv[4] ?? '30', 10) || 30;
  const ttl = Number.parseInt(process.argv[5] ?? String(INSTALLATION_TOKEN_TTL), 10)
    || INSTALLATION_TOKEN_TTL;
  const url = path.startsWith('/') ? API + path : path;

  const first = await get(url, token);
  if (first.status !== 200 || !first.etag) {
    console.error(`first GET ${url} returned ${first.status} with etag ` +
      `${first.etag}; pick a url that returns a validator`);
    process.exitCode = 2;
    return;
  }
  console.log(`cache key would be (${fingerprint(token)}, ${path})`);

  const control = await get(url, token, first.etag);
  console.log(`control: same credential, same etag -> ${control.status}`);

  let rotated = null;
  if (second) {
    rotated = (await get(url, second, first.etag)).status;
    console.log(`rotation: second credential, same etag -> ${rotated}`);
  } else {
    console.warn('set GITHUB_TOKEN_SECOND to a second credential to measure ' +
      'the rotation rather than project it');
  }

  const [state, detail] = classifyPair(control.status, rotated);
  console.log(`${state}: ${detail}`);

  const waste = rotationWaste(urls, interval, ttl);
  const [final, why] = verdict(state, waste);
  console.log(`${final}: ${why}`);

  if (final === 'rotation-dominates' || final === 'rotation-costs') {
    console.log('repair: key the cache by (credential fingerprint, url) so a ' +
      'rotation is an honest miss rather than a silent one.');
    console.log('repair: hold one installation token for its full hour and ' +
      'refresh a minute before expires_at, rather than minting a fresh one ' +
      'per request.');
  }

  console.log(JSON.stringify({
    measured: state, state: final, waste,
    used_before: first.used, used_control: control.used,
  }, null, 2));
  process.exitCode = (final === 'rotation-dominates' || final === 'rotation-costs' ||
    final === 'not-cacheable') ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main() and fail on the missing token.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
