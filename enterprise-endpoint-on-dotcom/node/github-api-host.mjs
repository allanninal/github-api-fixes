/**
 * Say which GitHub installation this client is actually talking to.
 *
 * Read only, and two of its three calls need no credential at all. Nothing is
 * configured or written: the repair is an environment variable and a startup
 * assertion, and both are printed.
 *
 * github.com, a GitHub Enterprise Server appliance and an Enterprise Cloud
 * tenant with data residency are separate installations. A credential from one
 * is meaningless at the others, and a base URL that defaults to the wrong one
 * produces a 404 on every route or a flat 401 on a fresh token.
 *
 * Environment:
 *   GITHUB_API_URL      the base URL the client resolved at startup
 *   GITHUB_TOKEN        optional; needed only for the identity assertion
 *   GITHUB_EXPECT_LOGIN optional account this credential should be here
 */
const UA = 'github-api-host/1.0';

export const DOTCOM_API_HOST = 'api.github.com';
export const DOTCOM_WEB_HOST = 'github.com';
export const GHES_REST_SUFFIX = '/api/v3';
export const GHES_GRAPHQL_SUFFIX = '/api/graphql';
export const RESIDENCY_SUFFIX = '.ghe.com';

export const FAMILIES = ['dotcom', 'enterprise-server',
  'enterprise-cloud-data-residency', 'web-host-not-api', 'unknown'];

/** [requests, unauthenticated ones] this run will spend. Pure. */
export function readCost(withIdentity) {
  return [2 + (withIdentity ? 1 : 0), 2];
}

/** Trim a base URL to a comparable form. Pure. */
export function normaliseBase(url) {
  let value = String(url ?? '').trim();
  while (value.endsWith('/')) value = value.slice(0, -1);
  return value;
}

/** The hostname in a URL, lowercased, or null. Pure. */
export function hostOf(url) {
  try {
    return new URL(String(url ?? '')).hostname.toLowerCase() || null;
  } catch {
    return null;
  }
}

/** Guess the installation family from the configured URL. Pure. */
export function familyFromUrl(base) {
  const value = normaliseBase(base);
  const host = hostOf(value);
  if (!host) return ['unknown', 'no host could be parsed out of the base URL.'];
  if (host === DOTCOM_API_HOST) {
    return ['dotcom', 'api.github.com is the github.com API host.'];
  }
  if (host === DOTCOM_WEB_HOST) {
    return ['web-host-not-api', 'github.com is the web interface. The API lives '
      + 'at api.github.com, and a client pointed here will be handed HTML.'];
  }
  if (host.startsWith('api.') && host.endsWith(RESIDENCY_SUFFIX)) {
    return ['enterprise-cloud-data-residency', 'an api.SUBDOMAIN.ghe.com host is '
      + 'an Enterprise Cloud tenant with data residency, which is its own '
      + 'installation.'];
  }
  if (value.endsWith(GHES_REST_SUFFIX)) {
    return ['enterprise-server', `a host with the ${GHES_REST_SUFFIX} suffix is `
      + 'an Enterprise Server appliance.'];
  }
  if (value.endsWith(GHES_GRAPHQL_SUFFIX)) {
    return ['enterprise-server', "this is the appliance's GraphQL path; its "
      + `REST base is ${GHES_REST_SUFFIX}.`];
  }
  return ['web-host-not-api', 'this host carries no API prefix. On an appliance '
    + `the REST base is the hostname plus ${GHES_REST_SUFFIX}, and without it `
    + 'you are talking to the web interface, which answers 200 and sends HTML.'];
}

/** Did the host send a web page. Pure. */
export function contentIsHtml(contentType) {
  return String(contentType ?? '').toLowerCase().includes('html');
}

/** What the host itself says it is. Pure. [family, detail]. */
export function familyFromMeta(status, contentType, body) {
  if (contentIsHtml(contentType)) {
    return ['web-host-not-api', 'the host returned HTML rather than JSON, so '
      + 'this is a web interface. A client checking only the status code sees a '
      + '200 here and reports success.'];
  }
  if (Number(status) !== 200 || !body || typeof body !== 'object') {
    return ['meta-unreadable', '/meta did not return a readable JSON document, '
      + 'so the host could not identify itself. On a private appliance this '
      + 'endpoint can require authentication.'];
  }
  const version = body.installed_version;
  if (version) {
    return ['enterprise-server', `installed_version is present (${version}), `
      + 'which the github.com schema for this endpoint does not carry.'];
  }
  if ('verifiable_password_authentication' in body || 'hooks' in body) {
    return ['dotcom-or-enterprise-cloud', 'a valid /meta document with no '
      + 'installed_version. That is github.com, or an Enterprise Cloud tenant, '
      + 'which are served from the same host and cannot be separated here.'];
  }
  return ['meta-unreadable', 'the document does not look like /meta, so nothing '
    + 'can be concluded from it.'];
}

/** The host named in the root endpoint map. Pure. [host, detail]. */
export function servedHostFromRoot(root) {
  if (!root || typeof root !== 'object' || Object.keys(root).length === 0) {
    return [null, 'the root endpoint map was not readable, so the host that '
      + 'answered cannot be named.'];
  }
  for (const key of ['current_user_url', 'repository_url', 'user_url']) {
    const value = root[key];
    const host = typeof value === 'string' ? hostOf(value) : null;
    if (host) return [host, `taken from ${key} in the root map.`];
  }
  for (const value of Object.values(root)) {
    const host = typeof value === 'string' ? hostOf(value) : null;
    if (host) return [host, 'taken from an absolute URL in the root map.'];
  }
  return [null, 'the root map carried no absolute URL to read a host from.'];
}

/** Compare three independent readings. Pure. [state, detail]. */
export function agreement(guessed, reported, configuredHost, servedHost) {
  if (reported === 'web-host-not-api' || guessed === 'web-host-not-api') {
    return ['no-api-prefix', 'this is a web interface rather than an API base. '
      + `On an appliance append ${GHES_REST_SUFFIX} to the hostname; on `
      + 'github.com use api.github.com.'];
  }
  if (servedHost && configuredHost && servedHost !== configuredHost) {
    return ['served-elsewhere', `you dialled ${configuredHost} and ${servedHost} `
      + 'answered. A redirect or a proxy is sending this client somewhere else, '
      + 'which reading the configuration would never have caught.'];
  }
  if (reported === 'meta-unreadable') {
    return ['host-unidentified', 'the host did not identify itself, so the '
      + 'family in the URL is the only evidence and it is a guess.'];
  }
  if (reported === 'enterprise-server' && guessed !== 'enterprise-server') {
    return ['wrong-host-family', `the URL looks like ${guessed} and the host `
      + 'reports itself as an Enterprise Server appliance. Those are different '
      + 'installations.'];
  }
  if (reported === 'dotcom-or-enterprise-cloud' && guessed === 'enterprise-server') {
    return ['wrong-host-family', 'the URL carries an appliance suffix and the '
      + 'host answering is not an appliance. Those are different installations.'];
  }
  return ['agrees', 'the family guessed from the URL, the family the host '
    + 'reports, and the host that actually answered are all the same installation.'];
}

/** Assert the account, because the token cannot be checked locally. Pure. */
export function identityCheck(status, login, htmlUrl, expectedLogin, servedHost) {
  const code = Number(status) || 0;
  if (code === 0) {
    return ['not-checked', 'no identity call was made, so nothing confirms the '
      + 'credential belongs to this installation.'];
  }
  if (code === 401) {
    return ['credential-not-of-this-host', 'the credential was rejected outright '
      + 'by this host. A token minted at a different installation is not a weak '
      + 'token here, it is not a token at all.'];
  }
  if (code !== 200) {
    return ['identity-unreadable', `HTTP ${status} from the identity call, so `
      + 'the account could not be read.'];
  }
  const urlHost = hostOf(htmlUrl);
  if (expectedLogin && String(login ?? '').toLowerCase() !== String(expectedLogin).toLowerCase()) {
    return ['wrong-account', `this host knows the credential as `
      + `${JSON.stringify(login)} and you expected ${JSON.stringify(expectedLogin)}. `
      + 'Same shape of secret, different installation.'];
  }
  if (urlHost && servedHost && urlHost !== servedHost
      && !urlHost.endsWith(servedHost) && !servedHost.endsWith(urlHost)) {
    return ['html-url-host-mismatch', `the account's html_url points at `
      + `${urlHost} while ${servedHost} answered, which is worth explaining `
      + 'before trusting either.'];
  }
  return ['identity-as-expected', 'the account this host returns is the one you '
    + 'expected.'];
}

/** State plainly that a prefix cannot name an installation. Pure. */
export function tokenShapeIsNoEvidence(token) {
  const value = String(token ?? '').trim();
  const known = ['github_pat_', 'ghp_', 'gho_', 'ghu_', 'ghs_', 'ghr_'];
  if (known.some((prefix) => value.startsWith(prefix))) {
    return ['class-known-host-unknown', 'the prefix names the credential class '
      + 'and never the installation that issued it. There is no local test for '
      + 'which host a token belongs to; the identity call is the only one.'];
  }
  return ['class-unknown', 'the credential class could not be named, and it '
    + 'would not have named the installation anyway.'];
}

/** The finding, in one state. Pure. [state, detail]. */
export function verdict(agreementState, identityState) {
  if (agreementState === 'no-api-prefix') {
    return ['no-api-prefix', 'the base URL is a web interface, so every API call '
      + 'is being answered with a web page.'];
  }
  if (agreementState === 'wrong-host-family') {
    return ['wrong-installation', 'the client is configured for one installation '
      + 'and talking to another. Every 404 and every 401 follows from that.'];
  }
  if (agreementState === 'served-elsewhere') {
    return ['redirected-elsewhere', 'the host that answered is not the host that '
      + 'was dialled, so the configuration is not the whole story.'];
  }
  if (['credential-not-of-this-host', 'wrong-account'].includes(identityState)) {
    return ['credential-from-another-host', 'the host is reachable and the '
      + 'credential does not belong to it, which is the same bug seen from the '
      + 'other side.'];
  }
  if (agreementState === 'host-unidentified') {
    return ['host-unidentified', 'the host would not identify itself, so this '
      + 'run narrows the question rather than answering it.'];
  }
  if (identityState === 'html-url-host-mismatch') {
    return ['host-mismatch-in-payload', 'the objects this host returns point at '
      + 'a different hostname from the one serving them.'];
  }
  return ['host-as-configured', 'the base URL, the host and the account all '
    + 'describe the same installation.'];
}

/** The sentence a reader has to act on. Pure. Nothing here is configured. */
export function repair(state, base) {
  if (state === 'no-api-prefix') {
    return `set the API base URL properly: https://${DOTCOM_API_HOST} for `
      + `github.com, the appliance hostname plus ${GHES_REST_SUFFIX} for `
      + 'Enterprise Server, and api.SUBDOMAIN.ghe.com for a data-residency tenant.';
  }
  if (['wrong-installation', 'credential-from-another-host'].includes(state)) {
    return 'set the base URL explicitly for this environment rather than letting '
      + 'a library default decide, and pair each base URL with the credential '
      + `minted at that installation. ${base || 'the configured base'} is not the `
      + 'host holding these resources.';
  }
  if (state === 'redirected-elsewhere') {
    return 'find out what is redirecting this client. Then assert at startup '
      + 'that the host in the root map matches the host you configured, so the '
      + 'next one is caught in a second.';
  }
  if (state === 'host-unidentified') {
    return 're-run with a credential this host accepts, or from a network that '
      + 'can reach it. A private appliance can require authentication even for /meta.';
  }
  return 'nothing to change. Keep this as a startup assertion rather than a '
    + 'thing somebody runs after a week of 404s.';
}

function headers(token) {
  const bag = {
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
  if (token) bag.Authorization = `Bearer ${token}`;
  return bag;
}

async function safeGet(url, token) {
  try {
    return await fetch(url, { headers: headers(token) });
  } catch (err) {
    console.warn(`${url} did not answer: ${err.message}`);
    return null;
  }
}

async function main() {
  const base = normaliseBase((process.env.GITHUB_API_UR || "dummy-github-api-ur")L || `https://${DOTCOM_API_HOST}`);
  const token = (process.env.GITHUB_TOKE || "dummy-github-toke")N || '';
  const expectLogin = (process.env.GITHUB_EXPECT_LOGI || "dummy-github-expect-logi")N || '';
  const [made, free] = readCost(Boolean(token));
  console.log(`read cost: ${made} REST request(s), ${free} of them `
    + 'unauthenticated and free');

  const configuredHost = hostOf(base);
  const [guessed, guessedDetail] = familyFromUrl(base);
  console.log(`configured: host ${configuredHost}, guessed family ${guessed}. ${guessedDetail}`);

  const meta = await safeGet(`${base}/meta`, '');
  let reported = 'meta-unreadable';
  let reportedDetail = 'the host did not answer.';
  if (meta) {
    let metaBody = null;
    try { metaBody = await meta.json(); } catch { metaBody = null; }
    [reported, reportedDetail] = familyFromMeta(meta.status,
      meta.headers.get('content-type') || '', metaBody);
  }
  console.log(`meta: ${reported}. ${reportedDetail}`);

  const root = await safeGet(`${base}/`, '');
  let rootBody = null;
  if (root) {
    try { rootBody = await root.json(); } catch { rootBody = null; }
  }
  const [servedHost, servedDetail] = servedHostFromRoot(rootBody);
  console.log(`served host: ${servedHost || 'unknown'} (${servedDetail})`);

  const [agreementState, agreementDetail] = agreement(guessed, reported,
    configuredHost, servedHost);
  console.log(`${agreementState}: ${agreementDetail}`);

  let identityState = 'not-checked';
  let identityDetail = 'no token supplied.';
  let login = null;
  if (token) {
    const who = await safeGet(`${base}/user`, token);
    if (who) {
      let body = {};
      try { body = (await who.json()) || {}; } catch { body = {}; }
      login = body.login ?? null;
      [identityState, identityDetail] = identityCheck(who.status, login,
        body.html_url, expectLogin, servedHost);
    }
    const [shapeState, shapeDetail] = tokenShapeIsNoEvidence(token);
    console.log(`${shapeState}: ${shapeDetail}`);
  }
  console.log(`identity: ${identityState}. ${identityDetail}`);

  const [state, detail] = verdict(agreementState, identityState);
  console.log(`${state}: ${detail}`);
  const fix = repair(state, base);
  console.log(`repair: ${fix}`);

  console.log(JSON.stringify({
    base,
    configured_host: configuredHost,
    guessed_family: guessed,
    reported_family: reported,
    served_host: servedHost,
    agreement_state: agreementState,
    identity_state: identityState,
    login,
    state,
    detail,
    repair: fix,
  }, null, 2));
  process.exitCode = state === 'host-as-configured' ? 0 : 1;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
