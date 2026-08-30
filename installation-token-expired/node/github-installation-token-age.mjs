/**
 * Say how much of its hour a GitHub App installation token has left.
 *
 * Read only. One request, GET /installation/repositories, which is the route
 * an installation access token can answer and almost nothing else can.
 *
 * Minting is a write, so this script never does it. The mint moment comes from
 * your own record of it, or the expiry comes from the header GitHub attaches
 * to a response for a credential that has one. The report says which source it
 * used.
 *
 * The token is read from the environment and never printed.
 */
const API = 'https://api.github.com';
const UA = 'github-installation-token-age/1.0';

/** Fixed, from the moment of minting, and not extended by use. */
export const LIFETIME = 3600;

/** Re-mint with this much of the hour still unspent. */
export const SAFE_MARGIN = 600;
export const RECOMMENDED_INTERVAL = LIFETIME - SAFE_MARGIN;

/** Under this, a long batch will cross the line while it is still working. */
export const DANGER_BAND = 300;

/** Two records of the same token should agree to about this. */
export const RECONCILE_TOLERANCE = 60;

/** Parse an epoch or an ISO-8601 timestamp into epoch seconds. Pure. */
export function parseMoment(value) {
  const text = String(value ?? '').trim();
  if (!text) return null;
  if (/^\d{9,11}$/.test(text)) return Number(text);
  const ms = Date.parse(text);
  return Number.isNaN(ms) ? null : ms / 1000;
}

/**
 * Parse the expiry GitHub puts on a response. Pure.
 * It is not ISO-8601: a space-separated date and time followed by a zone name.
 */
export function parseExpiryHeader(value) {
  let text = String(value ?? '').trim();
  if (!text) return null;
  if (text.endsWith(' UTC')) {
    text = `${text.slice(0, -4).trim().replace(' ', 'T')}+00:00`;
  }
  return parseMoment(text);
}

/**
 * Seconds of life left, and where the number came from. Pure.
 * GitHub's expiry wins when both exist: yours is a record of a mint, GitHub's
 * is a statement about the credential in your hand.
 */
export function remaining(mintedAt, expiresAt, now) {
  if (expiresAt !== null && expiresAt !== undefined) {
    return [Math.trunc(expiresAt - now), 'github'];
  }
  if (mintedAt !== null && mintedAt !== undefined) {
    return [Math.trunc(mintedAt + LIFETIME - now), 'record'];
  }
  return [null, 'nothing'];
}

/** Turn a remaining life into a band. Pure. */
export function classify(left) {
  if (left === null || left === undefined) {
    return ['no-record',
      'there is no mint time recorded and no expiry on the response, so ' +
      'nothing can be said about how much of the hour is left. Record the ' +
      'moment you mint, next to the token.'];
  }
  if (left <= 0) {
    return ['expired',
      `this token ran out ${-left}s ago. Every call made with it returns 401 ` +
      'Bad credentials, all at once, which is why a restart appears to fix it.'];
  }
  if (left < DANGER_BAND) {
    return ['inside-the-danger-band',
      `${left}s remain of the ${LIFETIME}s lifetime. A batch that runs longer ` +
      'than that will cross the line while it is still working.'];
  }
  if (left < SAFE_MARGIN) {
    return ['past-the-safe-margin',
      `${left}s remain, which is inside the ${SAFE_MARGIN}s margin a refresh ` +
      'needs to cover a slow mint and a retry.'];
  }
  return ['fresh', `${left}s remain of the ${LIFETIME}s lifetime.`];
}

/** Judge a refresh schedule against the fixed lifetime. Pure. */
export function refreshVerdict(interval) {
  if (!interval || interval <= 0) {
    return ['minted-once-at-startup',
      'no refresh interval, so this process mints once and holds. The first ' +
      `401 arrives ${LIFETIME / 60} minutes after start, on everything at once.`];
  }
  if (interval >= LIFETIME) {
    return ['refresh-slower-than-lifetime',
      `re-minting every ${interval}s against a ${LIFETIME}s lifetime is not a ` +
      'refresh, it is a race. Some days the token is replaced first and some ' +
      'days it is not.'];
  }
  if (interval > LIFETIME - SAFE_MARGIN) {
    return ['refresh-without-margin',
      `re-minting every ${interval}s leaves only ${LIFETIME - interval}s of ` +
      'margin, which one slow mint or one retry uses up.'];
  }
  return ['refresh-healthy',
    `re-minting every ${interval}s leaves ${LIFETIME - interval}s of margin.`];
}

/** The epoch second at which 401s begin, or null. Pure. */
export function cliffAt(mintedAt) {
  if (mintedAt === null || mintedAt === undefined) return null;
  return Math.trunc(mintedAt) + LIFETIME;
}

/** Compare GitHub's expiry against your own record of the mint. Pure. */
export function reconcile(headerExpiry, recordExpiry) {
  if (headerExpiry === null || headerExpiry === undefined) {
    return ['no-header',
      'the response carried no expiry, so GitHub\'s view is unavailable and ' +
      'only your record is in play.'];
  }
  if (recordExpiry === null || recordExpiry === undefined) {
    return ['header-only',
      'there is no recorded mint time to check GitHub\'s expiry against. ' +
      'Record one; it costs nothing and it is the only way to notice a stale ' +
      'token.'];
  }
  const gap = Math.trunc(Math.abs(headerExpiry - recordExpiry));
  if (gap <= RECONCILE_TOLERANCE) {
    return ['record-agrees',
      `GitHub's expiry and your recorded mint time are ${gap}s apart.`];
  }
  return ['record-disagrees',
    `GitHub's expiry and your recorded mint time are ${gap}s apart, so this ` +
    'process is not holding the token it recorded. Look for a cached token or ' +
    'two workers sharing one variable.'];
}

/** Map the live response to a cause, using the remaining life. Pure. */
export function interpret(status, message, left) {
  if (status === 200) {
    return ['token-live',
      'the token answered the installation route, so it is valid right now.'];
  }
  const text = String(message ?? '').toLowerCase();
  if (status === 401 && text.includes('bad credentials')) {
    if (left !== null && left !== undefined && left <= 0) {
      return ['expired-as-predicted',
        'the token is past its hour and GitHub refused it, which is exactly ' +
        'the arithmetic above.'];
    }
    if (left !== null && left !== undefined && left > DANGER_BAND) {
      return ['not-an-expiry-problem',
        `${left}s of the lifetime remain and GitHub still refused the token, ` +
        'so it was revoked, truncated or never valid. That is a different ' +
        'investigation.'];
    }
    return ['expired-or-revoked-cannot-tell',
      'GitHub refused the token and there is no reliable record of when it ' +
      'was minted, so expiry and revocation look identical from here.'];
  }
  if (status === 403 && text.includes('not accessible by integration')) {
    return ['wrong-credential-class',
      'this route accepted the credential and refused the action, which means ' +
      'what is being held is not an installation access token at all.'];
  }
  if (status === 404) {
    return ['route-not-answered',
      'a 404 on the installation route usually means the credential is not an ' +
      'installation access token.'];
  }
  return ['unrelated',
    'the response does not look like an expiry, so this failure has another cause.'];
}

function flag(name, fallback) {
  const at = process.argv.indexOf(name);
  if (at === -1 || at === process.argv.length - 1) return fallback;
  return process.argv[at + 1];
}

async function main() {
  const token = (process.env.GITHUB_INSTALLATION_TOKEN || "dummy-github-installation-token");
  if (!token) {
    console.error('set GITHUB_INSTALLATION_TOKEN to the installation access ' +
      'token the process is holding');
    process.exitCode = 2;
    return;
  }
  const interval = Number(flag('--refresh-interval', 0)) || 0;
  const mintedAt = parseMoment(flag('--minted-at', null)
    ?? (process.env.GITHUB_TOKEN_MINTED_AT || "dummy-github-token-minted-at"));
  const now = Date.now() / 1000;

  const res = await fetch(`${API}/installation/repositories?per_page=1`, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': UA,
    },
  });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  const message = body && typeof body === 'object' ? body.message : null;
  const headerExpiry = parseExpiryHeader(
    res.headers.get('github-authentication-token-expiration'));
  console.log(`GET /installation/repositories returned ${res.status}`);

  const [left, source] = remaining(mintedAt, headerExpiry, now);
  if (mintedAt !== null) console.log(`minted ${Math.trunc(now - mintedAt)}s ago`);
  if (left !== null) console.log(`${left}s left, according to the ${source}`);

  const [state, detail] = classify(left);
  console.log(`${state}: ${detail}`);

  const [planState, planDetail] = refreshVerdict(interval);
  console.log(`${planState}: ${planDetail}`);

  const recordExpiry = mintedAt === null ? null : mintedAt + LIFETIME;
  const [matchState, matchDetail] = reconcile(headerExpiry, recordExpiry);
  console.log(`${matchState}: ${matchDetail}`);

  const [liveState, liveDetail] = interpret(res.status, message, left);
  console.log(`${liveState}: ${liveDetail}`);

  if (planState !== 'refresh-healthy' || state === 'expired'
      || state === 'inside-the-danger-band') {
    console.log(`repair: re-mint every ${RECOMMENDED_INTERVAL}s, and re-mint ` +
      'again on any 401. A timer alone still fails on the day something stalls.');
  }

  console.log(JSON.stringify({
    seconds_left: left,
    source,
    cliff_at: cliffAt(mintedAt),
    refresh_interval: interval,
    state,
    refresh_state: planState,
    reconcile_state: matchState,
    live_state: liveState,
  }, null, 2));
  process.exitCode = (state === 'fresh' && planState === 'refresh-healthy') ? 0 : 1;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main() and set an exit code that fails a passing suite.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
