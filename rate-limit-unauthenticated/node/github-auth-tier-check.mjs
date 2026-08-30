/**
 * Prove which authentication tier your requests are actually in.
 *
 * Read only. Three GETs: /rate_limit with the token, /rate_limit without it as
 * a control, and /user. GET /rate_limit does not count against the primary
 * rate limit, so the check is free in both tiers.
 *
 * The token is read from the environment and never printed.
 */
const API = 'https://api.github.com';
const UA = 'github-auth-tier-check/1.0';

export const ANON_LIMIT = 60;

// Recognising a prefix is not proof the token is valid; it is evidence that the
// variable holds a token rather than a path, a URL or a leftover placeholder.
const PREFIXES = {
  ghp_: 'classic personal access token',
  github_pat_: 'fine-grained personal access token',
  gho_: 'OAuth app user token',
  ghu_: 'GitHub App user-to-server token',
  ghs_: 'GitHub App installation token',
  ghr_: 'GitHub App refresh token',
  eyJ: 'JSON Web Token, signed as a GitHub App',
};

const PLACEHOLDERS = ['your', 'xxx', '<', '>', 'changeme', 'replace', 'example',
  'placeholder', 'dummy', 'here', 'todo'];

/**
 * Describe the environment variable without disclosing it. Pure.
 * Unset and empty are different findings with different repairs, even though
 * both fail the same falsy check.
 */
export function inspectSecret(raw) {
  const problems = [];
  if (raw === null || raw === undefined) {
    return { fingerprint: 'absent', kind: null, problems: ['unset'] };
  }
  if (raw === '') return { fingerprint: 'empty string', kind: null, problems: ['empty'] };

  let value = raw.trim();
  if (!value) return { fingerprint: 'whitespace only', kind: null, problems: ['blank'] };
  if (value !== raw) problems.push('padded');

  if (value.length >= 2 && value[0] === value[value.length - 1] && '"\''.includes(value[0])) {
    problems.push('quoted');
    value = value.slice(1, -1).trim();
  }

  let lowered = value.toLowerCase();
  for (const scheme of ['bearer ', 'token ']) {
    if (lowered.startsWith(scheme)) {
      problems.push('scheme-included');
      value = value.slice(scheme.length).trim();
      lowered = value.toLowerCase();
      break;
    }
  }

  if (/\s/.test(value)) problems.push('contains-whitespace');

  let kind = null;
  for (const [prefix, name] of Object.entries(PREFIXES)) {
    if (value.startsWith(prefix)) { kind = name; break; }
  }

  if (kind === null) {
    problems.push('unknown-prefix');
    // Only after the prefix has already failed, so a real token containing
    // "xxx" by chance is not accused of being a placeholder.
    if (PLACEHOLDERS.some((m) => lowered.includes(m))) problems.push('placeholder');
  }

  const shown = Object.keys(PREFIXES).find((p) => value.startsWith(p)) ?? 'unrecognised';
  return { fingerprint: `${shown} (${value.length} chars)`, kind, problems };
}

/**
 * Name the tier a core limit belongs to. Pure.
 * Only the 60-against-anything-larger boundary is unambiguous; the rest is
 * colour, and 5,000 genuinely means two different things.
 */
export function tierFromLimit(limit) {
  const n = Number.parseInt(limit, 10);
  if (!Number.isFinite(n)) return ['unknown', 'no core limit was reported'];
  if (n <= 0) return ['unknown', `a core limit of ${n} is not a tier`];
  if (n <= ANON_LIMIT) {
    return ['anonymous',
      `a core limit of ${n} is the anonymous tier, which is counted per ` +
      'originating IP address and shared with everything else on it'];
  }
  if (n === 5000) {
    return ['authenticated',
      '5000 an hour: an authenticated user, an OAuth token, or a GitHub App ' +
      'installation that has not scaled beyond the floor'];
  }
  if (n === 15000) return ['enterprise', '15000 an hour: a user on GitHub Enterprise Cloud'];
  if (n > 5000) {
    return ['scaled',
      `${n} an hour, above the 5000 floor: a GitHub App installation whose ` +
      'limit has grown with installed repositories and users'];
  }
  return ['authenticated', `${n} an hour, which is above the anonymous 60`];
}

/**
 * Combine the local inspection and the two probes into one verdict. Pure.
 * "No token" and "a token GitHub refused" are not the same incident.
 */
export function diagnose(authedLimit, anonLimit, userStatus, secret) {
  const s = secret ?? { problems: ['unset'], fingerprint: 'absent' };
  const problems = s.problems ?? [];
  const [tier, note] = tierFromLimit(authedLimit);
  const [anonTier] = tierFromLimit(anonLimit);

  const missing = ['unset', 'empty', 'blank'].find((p) => problems.includes(p));
  if (missing) {
    const said = { unset: 'not set', empty: 'set to an empty string', blank: 'whitespace only' };
    return ['no-token',
      `GITHUB_TOKEN is ${said[missing]}, so every request goes out anonymous ` +
      'at 60 an hour per IP address. This is not a quota problem and spending ' +
      'less will not help it.'];
  }

  if (tier === 'anonymous') {
    let detail = note;
    if (anonTier === 'anonymous') {
      detail = 'the token was sent and GitHub still reports ' + note +
        '. The control request without any header reports the same, so the ' +
        'header is not arriving.';
    }
    let extra = '';
    if (problems.includes('scheme-included')) {
      extra = ' The variable itself starts with a scheme word, so the header ' +
        'was probably built as "Bearer Bearer ...".';
    } else if (problems.includes('quoted')) {
      extra = ' The variable still has its surrounding quotes, which become ' +
        'part of the header value.';
    } else if (problems.includes('padded') || problems.includes('contains-whitespace')) {
      extra = ' The variable carries whitespace, which is enough to make the ' +
        'header invalid.';
    }
    return ['anonymous', detail + extra];
  }

  if (userStatus === 401) {
    return ['token-rejected',
      `the variable holds ${s.kind ?? 'an unrecognised value'} but GET /user ` +
      'answered 401. The token is expired, revoked, or the header was removed ' +
      'between here and GitHub. That is not the same as a missing token.'];
  }

  if (userStatus === 403) {
    return ['blocked',
      `authenticated at ${note}, but GET /user answered 403. Look at org SSO ` +
      'authorisation and IP allow lists rather than at the tier.'];
  }

  if (userStatus === 200) {
    return ['authenticated',
      `${note}. The anonymous control reports ${anonLimit}, so the header is arriving.`];
  }

  return ['unclear',
    `core limit says ${note} but GET /user answered ${userStatus}, so the two ` +
    'probes do not agree. Treat the limit as the more reliable of the two.'];
}

async function get(url, token) {
  const headers = {
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  try {
    const res = await fetch(url, { headers });
    let body = null;
    try { body = await res.json(); } catch { body = null; }
    return [res.status, body, Object.fromEntries(res.headers.entries())];
  } catch (err) {
    console.error(`${url} failed: ${err.message}`);
    return [0, null, {}];
  }
}

const coreLimit = (body) => body?.resources?.core?.limit;

async function main() {
  const name = process.argv[2] ?? 'GITHUB_TOKEN';
  const raw = process.env[name];
  const secret = inspectSecret(raw);
  console.log(`${name}: ${secret.fingerprint}${secret.kind ? ', ' + secret.kind : ''}`);
  for (const problem of secret.problems) console.warn(`  variable problem: ${problem}`);

  let token = (raw ?? '').trim().replace(/^["']|["']$/g, '').trim();
  for (const scheme of ['Bearer ', 'bearer ', 'token ', 'Token ']) {
    if (token.startsWith(scheme)) { token = token.slice(scheme.length).trim(); break; }
  }

  const [authedStatus, authedBody, authedHeaders] = await get(`${API}/rate_limit`, token || null);
  const [anonStatus, anonBody] = await get(`${API}/rate_limit`);
  const [userStatus, userBody] = await get(`${API}/user`, token || null);

  const authed = authedStatus === 200 ? coreLimit(authedBody) : null;
  const anon = anonStatus === 200 ? coreLimit(anonBody) : null;
  console.log(`with the token:    core limit ${authed}`);
  console.log(`control, no token: core limit ${anon}`);
  console.log(`GET /user:         ${userStatus}${userStatus === 200 ? ' as ' + userBody?.login : ''}`);

  const lowered = {};
  for (const [k, v] of Object.entries(authedHeaders)) lowered[k.toLowerCase()] = v;
  if (lowered['x-oauth-scopes'] !== undefined) {
    console.log(`x-oauth-scopes is present (${lowered['x-oauth-scopes'] || 'empty'}), so ` +
      'this is a classic token or an OAuth token rather than a fine-grained one');
  }

  const [state, detail] = diagnose(authed, anon, userStatus, secret);
  console.log(`${state}: ${detail}`);

  if (state !== 'authenticated') {
    console.log('repair: export the token where the process can see it. In a ' +
      'container that means passing it in, not exporting it in the shell that ' +
      'ran the build.');
    console.log('repair: paste the value only. No surrounding quotes, no Bearer ' +
      'prefix, no trailing newline from the file it came out of.');
    console.log('repair: assert the tier at startup rather than asserting the ' +
      'variable is non-empty:');
    console.log("  const { resources } = await (await fetch(`${API}/rate_limit`, { headers })).json();");
    console.log(`  if (resources.core.limit <= ${ANON_LIMIT}) throw new Error('unauthenticated');`);
  }

  console.log(JSON.stringify({
    state, fingerprint: secret.fingerprint, problems: secret.problems,
    authenticated_limit: authed, anonymous_limit: anon,
    user_status: userStatus, tier: tierFromLimit(authed)[0],
  }, null, 2));
  process.exitCode = state === 'authenticated' ? 0 : 1;
}

// Only run when invoked directly, so importing this from the test file does not
// start main() and set an exit code the tests never asked for.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
