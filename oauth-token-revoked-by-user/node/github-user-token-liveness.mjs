/**
 * Tell an individual OAuth revocation apart from an application wide one.
 *
 * Read only. One GET /user per stored user token. Nothing here mints,
 * refreshes or revokes anything; the repair is a URL printed for the affected
 * person to open.
 *
 * The reading is a population, not a request. One refusal among many successes
 * is that user's decision. Every refusal at once is the application. No single
 * response can tell those apart.
 *
 * The definitive per-token check lives at /applications/{client_id}/token,
 * which is a write shaped call needing the client secret. This script does not
 * hold application secrets and does not make that call.
 */
const API = 'https://api.github.com';
const UA = 'github-user-token-liveness/1.0 (+https://example.com/contact)';
const AUTHORIZE = 'https://github.com/login/oauth/authorize';

/** Gather stored user tokens out of a mapping by prefix. Pure. */
export function collectTokens(environ, prefix) {
  return Object.entries(environ ?? {})
    .filter(([name, value]) => name.startsWith(prefix) && value)
    .sort(([a], [b]) => a.localeCompare(b));
}

/** Classify one liveness probe. Pure. A 403 is not a revocation. */
export function tokenResult(status) {
  if (status === 200) return 'alive';
  if (status === 401) return 'rejected';
  if (status === 403) return 'forbidden';
  return 'error';
}

/** Read the fleet rather than the request. Pure. */
export function populationVerdict(results) {
  if (!results || !results.length) {
    return ['no-tokens',
      'nothing was collected, so there is nothing to read. Check the prefix ' +
      'the variables are named with.'];
  }
  const alive = results.filter(([, s]) => s === 'alive').map(([n]) => n);
  const rejected = results.filter(([, s]) => s === 'rejected').map(([n]) => n);
  if (!rejected.length) {
    return ['all-healthy',
      'every stored token is accepted, so no authorization has been revoked. ' +
      'Whatever you are chasing is somewhere else.'];
  }
  if (results.length === 1) {
    return ['single-token-inconclusive',
      'one token is stored and it is refused. That is consistent with this ' +
      'user revoking, and equally consistent with the application being ' +
      'suspended or its secret rotated. With one sample the two cannot be ' +
      'separated.'];
  }
  if (alive.length) {
    return ['individual-revocation',
      `${rejected.length} of ${results.length} stored tokens are refused ` +
      'while others work, so this is those people\'s decision rather than an ' +
      `application problem: ${rejected.join(', ')}`];
  }
  return ['application-wide',
    `all ${results.length} stored tokens are refused at once. Users do not ` +
    'coordinate revocations. Look at the application: a rotated client ' +
    'secret, a suspended app, or an organization owner removing the approval ' +
    'for the whole cohort.'];
}

/** Say whether a state should ever be retried. Pure. */
export function retryDisposition(state) {
  if (state === 'rejected') {
    return ['terminal',
      'a revoked or invalid user token never recovers on its own. Mark the ' +
      'connection broken, take it off the schedule, and ask the person to ' +
      'authorize again.'];
  }
  if (state === 'forbidden') {
    return ['terminal',
      'the credential was accepted and the action was refused. Retrying ' +
      'changes nothing; this is an access question.'];
  }
  if (state === 'error') {
    return ['retryable',
      'the probe itself did not complete, so nothing is known about the ' +
      'credential. This one is worth trying again.'];
  }
  return ['none', 'nothing to retry.'];
}

/** Build the URL that starts the authorization flow again. Pure. */
export function authorizeUrl(clientId, scopes = [], redirectUri = null, state = null) {
  const params = new URLSearchParams([['client_id', clientId]]);
  if (scopes && scopes.length) params.append('scope', scopes.join(' '));
  if (redirectUri) params.append('redirect_uri', redirectUri);
  if (state) params.append('state', state);
  return `${AUTHORIZE}?${params.toString()}`;
}

async function probe(token) {
  const res = await fetch(`${API}/user`, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': UA,
    },
  });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  const login = body && typeof body === 'object' ? body.login ?? null : null;
  return [res.status, login];
}

function arg(name, fallback) {
  const i = process.argv.indexOf(name);
  return i === -1 ? fallback : process.argv[i + 1];
}

async function main() {
  const prefix = arg('--env-prefix', 'GH_USER_TOKEN_');
  const scopes = arg('--scopes', '');

  const stored = collectTokens(process.env, prefix);
  if (!stored.length) {
    console.error(`no variables found with the prefix ${prefix}. Store one ` +
      'token per connection so the set can be read as a set');
    process.exitCode = 2;
    return;
  }

  const clientId = (process.env.GITHUB_OAUTH_CLIENT_ID || "dummy-github-oauth-client-id") ?? '';
  const results = [];
  const findings = [];
  for (const [name, token] of stored) {
    const [status, login] = await probe(token);
    const state = tokenResult(status);
    results.push([name, state]);
    console.log(`${name.padEnd(24)} ${state.padEnd(9)} ${login ?? '-'}`);
    findings.push({ env: name, state, login, status });
  }

  const [verdict, detail] = populationVerdict(results);
  console.log(`${verdict}: ${detail}`);

  for (const [name, state] of results) {
    if (state === 'alive') continue;
    const [disposition, why] = retryDisposition(state);
    console.log(`${name}: ${disposition}. ${why}`);
  }

  if (verdict === 'individual-revocation' || verdict === 'single-token-inconclusive') {
    if (clientId) {
      const url = authorizeUrl(clientId, scopes ? scopes.split(' ').filter(Boolean) : []);
      console.log(`repair: send the affected people through the flow again: ${url}`);
    } else {
      console.log('repair: set GITHUB_OAUTH_CLIENT_ID to have the authorize ' +
        'URL printed here.');
    }
  }
  if (verdict === 'application-wide') {
    console.log('repair: this is not the users. Check whether the client ' +
      'secret was rotated, whether the application is suspended, and whether ' +
      'an organization owner removed its approval.');
  }

  console.log(JSON.stringify({ verdict, tokens: findings }, null, 2));
  process.exitCode = verdict === 'all-healthy' ? 0 : 1;
}

// Only run when invoked directly, so importing this module from the test file
// does not fire live requests and set an exit code the suite then inherits.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
