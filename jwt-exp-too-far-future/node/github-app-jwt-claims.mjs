/**
 * Audit the iat and exp claims of a GitHub App JWT before GitHub refuses them.
 *
 * Read only, and mostly offline. The JWT is read from the environment,
 * decoded locally, and never printed: the report contains three claim values
 * and a number of seconds.
 *
 * Decoding needs no key; verification would. The single request is GET /app,
 * which confirms the local verdict. The script stops there on purpose:
 * exchanging a JWT for an installation access token is a write, and nothing
 * in this section writes.
 */
const API = 'https://api.github.com';
const UA = 'github-app-jwt-claims/1.0';

/** The server-enforced maximum lifetime, and the values worth using instead. */
export const CEILING = 600;
export const RECOMMENDED_LIFETIME = 540;
export const RECOMMENDED_BACKDATE = 60;

/** How far ahead of the local clock iat may sit before it is worth reporting. */
export const SKEW_GRACE = 30;

/** Base64url-decode one JWT segment into an object. Pure. null on anything odd. */
export function decodeSegment(segment) {
  try {
    const raw = Buffer.from(String(segment ?? ''), 'base64url').toString('utf8');
    const value = JSON.parse(raw);
    return (value && typeof value === 'object' && !Array.isArray(value)) ? value : null;
  } catch {
    return null;
  }
}

/**
 * Split a JWT and decode its header and payload. Pure.
 * The signature segment is counted and then discarded without being decoded,
 * returned or logged.
 */
export function claims(jwt) {
  const parts = String(jwt ?? '').trim().split('.');
  if (parts.length !== 3) return [null, null];
  return [decodeSegment(parts[0]), decodeSegment(parts[1])];
}

const numeric = (v) => typeof v === 'number' && Number.isFinite(v);

/** The requested lifetime in seconds, or null. Pure. */
export function lifetime(payload) {
  if (!payload || typeof payload !== 'object') return null;
  if (!numeric(payload.iat) || !numeric(payload.exp)) return null;
  return Math.trunc(payload.exp) - Math.trunc(payload.iat);
}

/** How far iat sits from the local clock, in seconds. Negative is backdated. Pure. */
export function skew(payload, now) {
  if (!payload || typeof payload !== 'object' || !numeric(payload.iat)) return null;
  return Math.trunc(payload.iat) - Math.trunc(now);
}

/**
 * Turn a decoded payload and a clock reading into a finding. Pure.
 * The ceiling is checked before anything clock-relative, because a lifetime
 * over 600 seconds is wrong whatever time it is.
 */
export function audit(payload, now) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    return ['unreadable',
      'the middle segment did not decode to a JSON object, so this is not a ' +
      'well-formed JWT. Check what the signing code returned before looking ' +
      'at any claim.'];
  }
  if (!('iat' in payload)) {
    return ['no-iat',
      'there is no iat claim. GitHub measures the lifetime from it, so a JWT ' +
      'without one cannot be judged against the ten minute ceiling and is refused.'];
  }
  if (!('exp' in payload)) {
    return ['no-exp',
      'there is no exp claim, so the JWT never expires as far as the payload ' +
      'is concerned. That is exactly what the ceiling exists to prevent, and ' +
      'it is refused.'];
  }

  const span = lifetime(payload);
  if (span === null) {
    return ['non-numeric-claim',
      'iat and exp must be numeric seconds since the epoch. One of them is ' +
      'not a number, which usually means a date string or a millisecond ' +
      'timestamp went in where seconds were expected.'];
  }
  if (span <= 0) {
    return ['exp-not-after-iat',
      `exp is ${-span} second(s) before iat, so the JWT is expired at the ` +
      'moment it is signed.'];
  }
  if (span > CEILING) {
    return ['exp-too-far-future',
      `the requested lifetime is ${span}s, which is ${span - CEILING}s over ` +
      `the ${CEILING}s ceiling. Remove ${span - RECOMMENDED_LIFETIME}s from ` +
      'exp and the claim is legal.'];
  }

  const drift = skew(payload, now);
  const exp = Math.trunc(payload.exp);
  if (exp <= Math.trunc(now)) {
    return ['already-expired',
      `the lifetime is legal at ${span}s, and this JWT expired ` +
      `${Math.trunc(now) - exp}s ago. A JWT minted once and cached for the ` +
      'life of a process fails exactly like this, minutes after a deploy that ' +
      'looked fine.'];
  }
  if (drift !== null && drift > SKEW_GRACE) {
    return ['iat-in-the-future',
      `the lifetime is legal at ${span}s, and iat is ${drift}s ahead of this ` +
      'clock. If the signing machine is ahead of GitHub, iat lands in its ' +
      'future and the message names iat rather than exp. That is a different ' +
      'repair: backdate iat and fix the clock.'];
  }
  if (exp - Math.trunc(now) < 30) {
    return ['expiring-imminently',
      `the lifetime is legal at ${span}s and only ${exp - Math.trunc(now)}s of ` +
      'it remain, which is not enough to survive a retry. Mint per exchange ' +
      'rather than caching.'];
  }
  return ['within-ceiling',
    `the requested lifetime of ${span}s is inside the ${CEILING}s ceiling.`];
}

/** The claim values that would have worked. Pure. */
export function recommend(payload, now) {
  const iat = Math.trunc(now) - RECOMMENDED_BACKDATE;
  const span = lifetime(payload);
  return {
    iat,
    exp: iat + RECOMMENDED_LIFETIME,
    lifetime: RECOMMENDED_LIFETIME,
    seconds_to_remove: Math.max((span ?? 0) - RECOMMENDED_LIFETIME, 0),
  };
}

/** Map a live GET /app response to the defect it names. Pure. */
export function interpret(status, message) {
  if (status === 200) {
    return ['accepted', 'the JWT was accepted, so exp and iat are not the problem.'];
  }
  const text = String(message ?? '').toLowerCase();
  if (text.includes('too far in the future')) {
    return ['exp-too-far-future',
      'GitHub says exp is too far ahead of iat, which is the ceiling.'];
  }
  if (text.includes('issued at') || text.includes("'iat'")) {
    return ['iat-in-the-future',
      'GitHub says iat is in its future, which is clock drift on the signing ' +
      'machine rather than a lifetime problem.'];
  }
  if (text.includes('numeric value representing the future') || text.includes('expired')) {
    return ['already-expired',
      'GitHub says exp is not in the future, so this JWT was minted too long ' +
      'ago or the clock is behind.'];
  }
  if (text.includes('could not be decoded')) {
    return ['undecodable',
      'GitHub could not decode the JWT at all, which is a signing or encoding ' +
      'fault rather than a claim one.'];
  }
  if (text.includes('integration not found')) {
    return ['wrong-app-or-key',
      'the claims are acceptable and the App they name cannot be found, so ' +
      'iss or the signing key belongs to something else.'];
  }
  return ['unrelated',
    'the response does not mention a claim, so this failure has another cause.'];
}

async function main() {
  const jwt = (process.env.GITHUB_APP_JWT || "dummy-github-app-jwt");
  if (!jwt) {
    console.error('set GITHUB_APP_JWT to the JWT your own signing code ' +
      'produces. A JWT minted by this script would prove nothing about yours');
    process.exitCode = 2;
    return;
  }
  const offline = process.argv.includes('--offline');
  const now = Date.now() / 1000;
  const [header, payload] = claims(jwt);
  if (!payload) {
    console.error('the JWT did not decode into three segments with a JSON ' +
      'payload in the middle');
    const [state, detail] = audit(null, now);
    console.log(`${state}: ${detail}`);
    process.exitCode = 1;
    return;
  }

  // Claim values only. The signature is never decoded and the JWT is never
  // printed, in whole or in part.
  console.log(`iss=${payload.iss ?? 'absent'} iat=${payload.iat ?? 'absent'} ` +
    `exp=${payload.exp ?? 'absent'} lifetime=${lifetime(payload) ?? 'unknown'}s ` +
    `skew=${skew(payload, now) ?? 'unknown'}s`);
  if (header && header.alg && header.alg !== 'RS256') {
    console.log(`note: alg is ${header.alg} rather than RS256, which is a ` +
      'different defect from this one');
  }

  const [state, detail] = audit(payload, now);
  console.log(`${state}: ${detail}`);

  if (!offline) {
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

  const broken = ['exp-too-far-future', 'no-exp', 'no-iat', 'exp-not-after-iat',
    'non-numeric-claim', 'already-expired', 'expiring-imminently'];
  if (broken.includes(state)) {
    const want = recommend(payload, now);
    console.log(`repair: set iat=${want.iat} (now minus ${RECOMMENDED_BACKDATE}s) ` +
      `and exp=${want.exp} (iat plus ${want.lifetime}s), then mint a fresh JWT ` +
      'per token exchange rather than caching one');
    if (want.seconds_to_remove) {
      console.log(`repair: that is ${want.seconds_to_remove} second(s) off the ` +
        'current exp');
    }
  }

  console.log(JSON.stringify({
    iss: payload.iss ?? null,
    iat: payload.iat ?? null,
    exp: payload.exp ?? null,
    lifetime: lifetime(payload),
    skew_seconds: skew(payload, now),
    state,
  }, null, 2));
  process.exitCode = state === 'within-ceiling' ? 0 : 1;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main() and set an exit code that fails a passing suite.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
