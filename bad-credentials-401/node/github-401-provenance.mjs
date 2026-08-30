/**
 * Say which layer produced a 401, using two messages and one control request.
 *
 * Read only. Three GETs: the REST root with the credential, the REST root with
 * no credential at all, and GET /user.
 *
 * "Bad credentials" means a value was received and refused. "Requires
 * authentication" means nothing was received. The control request is what makes
 * the first of those provable rather than assumed.
 */
const API = 'https://api.github.com';
const UA = 'github-401-provenance/1.0';

// The REST root: readable by any anonymous caller, which is what makes it a
// clean place to attach a broken credential.
export const PUBLIC_PATH = '/';

export const BAD_CREDENTIALS = 'bad credentials';
export const REQUIRES_AUTH = 'requires authentication';

export const GITHUB_FURNITURE = [
  'x-github-request-id', 'x-github-media-type', 'x-github-api-version-selected',
];

/** The message GitHub put in the body, folded to lower case. Pure. */
export function messageOf(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return null;
  const value = body.message;
  if (typeof value !== 'string' || !value.trim()) return null;
  return value.trim().toLowerCase();
}

/** Whether GitHub itself answered, rather than something in front of it. Pure. */
export function fromGithub(headers) {
  const lowered = {};
  for (const [k, v] of Object.entries(headers ?? {})) lowered[String(k).toLowerCase()] = v;
  for (const name of GITHUB_FURNITURE) {
    if (lowered[name]) return [true, name];
  }
  if (String(lowered.server ?? '').toLowerCase().includes('github')) return [true, 'server'];
  return [false, null];
}

/** Reduce one probe to a symbol. Pure. The two 401s get different symbols. */
export function rung(status, message) {
  const code = Number.parseInt(status, 10);
  if (!Number.isFinite(code) || code === 0) return 'error';
  if (code >= 200 && code < 300) return 'ok';
  if (code === 401) {
    if (message === BAD_CREDENTIALS) return 'rejected';
    if (message === REQUIRES_AUTH) return 'anonymous';
    return 'unlabelled-401';
  }
  if (code === 403) return 'forbidden';
  return `http-${code}`;
}

/** Name the layer that produced the 401. Pure. */
export function diagnose(publicWith, publicWithout, user, expectedLogin = null) {
  const symbol = (probe) => rung((probe ?? {}).status, (probe ?? {}).message);
  const withHeader = symbol(publicWith);
  const withoutHeader = symbol(publicWithout);
  const identity = symbol(user);

  if (['rejected', 'anonymous', 'unlabelled-401'].includes(withHeader)
      && !(publicWith ?? {}).github) {
    return ['not-github',
      "the 401 carried none of GitHub's response furniture: no request id, no " +
      'media type, no GitHub server header. Something between this process and ' +
      'api.github.com answered, and it is not looking at your credential. ' +
      'Re-minting will not help.'];
  }

  if (withoutHeader === 'error') {
    return ['no-baseline',
      'the control request, which carries no credential at all, could not be ' +
      'made. Without it nothing below can be separated from a network fault.'];
  }

  if (withoutHeader !== 'ok') {
    return ['anonymous-refused',
      `the control request carries no credential and was still refused (${withoutHeader}). ` +
      'Whatever is producing this is not reading your token: look at IP allow ' +
      'lists, egress proxies and the network before you look at the credential.'];
  }

  if (withHeader === 'rejected') {
    return ['credential-rejected',
      'GitHub parsed the value and refused it. An endpoint that needs no ' +
      'credential at all answered 200 without the header and 401 with it, so ' +
      'the value being sent is the thing being rejected: expired, revoked, ' +
      'truncated, or from an account that no longer exists. That is a re-mint, ' +
      'not a network change.'];
  }

  if (identity === 'anonymous') {
    return ['header-not-arriving',
      'GET /user answered 401 Requires authentication, which is the message for ' +
      'a request that carried nothing. The header is being lost between here ' +
      'and GitHub: a redirect that dropped it, a client that only applies auth ' +
      'to configured hosts, or a proxy that strips what it does not recognise.'];
  }

  if (identity === 'rejected' && withHeader === 'ok') {
    return ['path-dependent',
      'the public endpoint accepted or ignored the same credential that GET ' +
      '/user refused. Two requests from the same process are not arriving as ' +
      'the same request, which points at something rewriting them in between.'];
  }

  if (identity === 'rejected') {
    return ['credential-rejected',
      'GET /user answered 401 Bad credentials, so the value was received and refused.'];
  }

  if (identity === 'forbidden') {
    return ['authenticated-but-forbidden',
      'the credential is valid and GET /user answered 403. That is not a bad ' +
      'credential: look at SSO authorisation, IP allow lists and organization policy.'];
  }

  if (identity === 'ok') {
    const login = (user ?? {}).login;
    if (expectedLogin && String(login ?? '').toLowerCase() !== String(expectedLogin).toLowerCase()) {
      return ['wrong-account',
        `the credential is valid and belongs to '${login}', not to the expected ` +
        `'${expectedLogin}'. A valid token for the wrong identity produces 404s ` +
        'and 403s all over an integration and never once says the word credentials.'];
    }
    return ['credential-valid',
      `the credential authenticates as '${login ?? 'an unnamed account'}'. ` +
      'Whatever is returning 401 is not this credential on this host, so look ' +
      'at the other variable, the other host, or the other process.'];
  }

  return ['unclear',
    `the three probes do not agree: root with header ${withHeader}, root without ` +
    `header ${withoutHeader}, /user ${identity}. Report the request id from the ` +
    'failing response rather than guessing.'];
}

async function probe(path, token = null) {
  const headers = {
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  let res;
  try {
    res = await fetch(API + path, { headers });
  } catch (err) {
    console.error(`GET ${path} failed: ${err.message}`);
    return { status: 0, message: null, github: false, login: null, request_id: null };
  }
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  const raw = {};
  for (const [k, v] of res.headers.entries()) raw[k.toLowerCase()] = v;
  const [isGithub, which] = fromGithub(raw);
  return {
    status: res.status,
    message: messageOf(body),
    github: isGithub,
    github_signal: which,
    login: body && typeof body === 'object' ? body.login ?? null : null,
    request_id: raw['x-github-request-id'] ?? null,
  };
}

async function main() {
  const envName = (process.env.GITHUB_TOKEN_EN || "dummy-github-token-en")V || 'GITHUB_TOKEN';
  const token = process.env[envName];
  if (!token) {
    console.error(`${envName} is not set, so there is no credential to account ` +
      'for. That is a different note: every request goes out anonymous.');
    process.exitCode = 2;
    return;
  }
  const expectedLogin = process.argv[2] ?? null;

  const publicWith = await probe(PUBLIC_PATH, token);
  const publicWithout = await probe(PUBLIC_PATH, null);
  const user = await probe('/user', token);

  console.log(`public endpoint with the header:    ${publicWith.status} ${publicWith.message ?? ''}`);
  console.log(`public endpoint without any header: ${publicWithout.status} ${publicWithout.message ?? ''}`);
  console.log(`GET /user:                          ${user.status} ${user.message ?? ''}`);
  if (!publicWith.github) {
    console.warn("the credentialled response carried none of GitHub's response furniture");
  }
  for (const [name, result] of [['root', publicWith], ['user', user]]) {
    if (result.request_id) console.log(`${name} request id ${result.request_id}`);
  }

  const [state, detail] = diagnose(publicWith, publicWithout, user, expectedLogin);
  console.log(`${state}: ${detail}`);

  if (state === 'credential-rejected') {
    console.log('repair: re-mint the credential, store it with no surrounding ' +
      'whitespace or quotes, and assert at startup that GET /user returns 200.');
  }
  if (state === 'header-not-arriving') {
    console.log('repair: log the outgoing request headers at the transport layer ' +
      'and check the tier as well; a stripped header means 60 an hour, not zero.');
  }
  if (state === 'wrong-account') {
    console.log('repair: assert the expected login at startup. It is three lines ' +
      'and it costs one free request.');
  }

  console.log(JSON.stringify({
    state,
    public_with: publicWith.status,
    public_without: publicWithout.status,
    user: user.status,
  }, null, 2));
  process.exitCode = state === 'credential-valid' ? 0 : 1;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err); process.exitCode = 2; });
}
