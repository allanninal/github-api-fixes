/**
 * Say whether a GitHub App is installed on one specific repository.
 *
 * Read only. Three GETs: one unauthenticated existence check, and two
 * presence questions asked with the App's JWT, at repository scope and at
 * account scope. Nothing is installed, added, changed or minted.
 *
 * An installation access token cannot see outside its installation, and
 * GitHub answers 404 rather than 403 for anything outside it, so a public
 * repository the App was never installed on is indistinguishable from one
 * that does not exist. GET /repos/{owner}/{repo}/installation answers
 * directly.
 *
 * The JWT is read from the environment and never printed.
 */
const API = 'https://api.github.com';
const UA = 'github-app-installation-presence/1.0';

/** Owner and repository names GitHub will actually issue. */
const NAME = /^[A-Za-z0-9._-]+$/;

/** Every way a repository reference tends to arrive in a bug report. */
const PREFIXES = ['https://github.com/', 'http://github.com/',
  'https://api.github.com/repos/', 'git@github.com:'];

/**
 * Reduce any repository reference to [owner, name]. Pure.
 * null when it is not a repository reference at all.
 */
export function splitRepo(value) {
  let text = String(value ?? '').trim().replace(/\/+$/, '');
  for (const prefix of PREFIXES) {
    if (text.startsWith(prefix)) {
      text = text.slice(prefix.length);
      break;
    }
  }
  if (text.endsWith('.git')) text = text.slice(0, -4);
  const parts = text.split('/').filter(Boolean);
  if (parts.length < 2) return null;
  const [owner, name] = parts;
  if (!NAME.test(owner) || !NAME.test(name)) return null;
  return [owner, name];
}

/**
 * The account-scope installation route for this kind of owner. Pure.
 * The wrong one 404s for reasons that have nothing to do with the App.
 */
export function accountRoute(owner, ownerType) {
  if (String(ownerType ?? '').toLowerCase() === 'user') {
    return `/users/${owner}/installation`;
  }
  return `/orgs/${owner}/installation`;
}

/** Parse an ISO-8601 timestamp into epoch seconds. Pure. null if unusable. */
export function parseIso(value) {
  const text = String(value ?? '').trim();
  if (!text) return null;
  const ms = Date.parse(text);
  return Number.isNaN(ms) ? null : ms / 1000;
}

/** What the unauthenticated read proved about the repository. Pure. */
export function visibility(status) {
  if (status === 200) {
    return ['public-repo',
      'the repository exists and is publicly readable, so whatever is wrong ' +
      'is on your side of the request.'];
  }
  if (status === 404) {
    return ['not-public-or-absent',
      'an unauthenticated read also returns 404, which means the repository ' +
      'is private or does not exist. A read-only check cannot separate those ' +
      'two, and neither can anyone else without access.'];
  }
  return ['visibility-unknown',
    'the unauthenticated read returned something other than 200 or 404, so ' +
    'nothing was established about the repository itself.'];
}

/**
 * Turn two presence questions into one verdict. Pure.
 * Repository scope first: a 200 there ends the matter. The account answer is
 * what splits one 404 into two different repairs.
 */
export function classify(repoStatus, accountStatus) {
  if ([401, 403].includes(repoStatus) || [401, 403].includes(accountStatus)) {
    return ['jwt-not-accepted',
      'the App JWT was refused, so nothing was learned about any ' +
      'installation. Fix the JWT first; a signing or clock fault looks like ' +
      'an absent installation from here.'];
  }
  if (repoStatus === 200) {
    return ['installed-on-this-repo',
      'the App is installed on this repository, so a 404 from your ' +
      'integration is about something else: a permission, a wrong path, or a ' +
      'credential that is not this App\'s.'];
  }
  if (repoStatus === 404 && accountStatus === 200) {
    return ['installed-on-account-not-repo',
      'the App is installed on the account and this repository is not in the ' +
      'installation. The installation is set to selected repositories and ' +
      'this one was never selected.'];
  }
  if (repoStatus === 404 && accountStatus === 404) {
    return ['not-installed-on-account',
      'the App is not installed anywhere on this account. Somebody with ' +
      'admin rights on the account has to install it; no permission or token ' +
      'change will do anything until they do.'];
  }
  return ['inconclusive',
    'the two presence checks did not return a pair this check recognises, so ' +
    'no verdict is safe.'];
}

/**
 * Say whether the repository is simply newer than the installation. Pure.
 * This is the difference between a mistake and a recurring condition.
 */
export function creationOrder(repoCreated, installationCreated, selection) {
  if (String(selection ?? '').toLowerCase() === 'all') {
    return ['selection-covers-everything',
      'repository_selection is all, so new repositories are covered ' +
      'automatically and creation order is irrelevant.'];
  }
  if (!selection) {
    return ['selection-unknown',
      'no repository_selection was returned, so nothing can be said about ' +
      'how the installation grows.'];
  }
  if (repoCreated === null || repoCreated === undefined
      || installationCreated === null || installationCreated === undefined) {
    return ['creation-order-unknown',
      'one of the two creation dates is missing, so the order cannot be ' +
      'established.'];
  }
  if (repoCreated > installationCreated) {
    const days = Math.trunc((repoCreated - installationCreated) / 86400);
    return ['repo-created-after-installation',
      `this repository was created ${days} day(s) after the installation, and ` +
      'a selected-repositories installation does not gain new ones. Every ' +
      'repository created from now on will land in the same state.'];
  }
  return ['repo-predates-installation',
    'the repository already existed when the installation was configured, so ' +
    'it was left out deliberately or by oversight rather than by the passage ' +
    'of time.'];
}

/** The sentence worth printing under a verdict. Pure. */
export function repairFor(state, selection) {
  if (state === 'installed-on-account-not-repo') {
    if (String(selection ?? '').toLowerCase() === 'selected') {
      return 'add this repository to the installation, or switch the ' +
        'installation to all repositories so future ones are covered without ' +
        'anybody remembering to.';
    }
    return 'open the installation\'s configuration and add this repository to it.';
  }
  if (state === 'not-installed-on-account') {
    return 'install the App on this account. This needs somebody with admin ' +
      'rights on the account, and it is not something a token change can ' +
      'substitute for.';
  }
  if (state === 'installed-on-this-repo') {
    return 'nothing to repair here. If calls still fail, read the status code ' +
      'and the message rather than assuming coverage.';
  }
  if (state === 'jwt-not-accepted') {
    return 'fix the App JWT before reading anything above; an unusable JWT ' +
      'and an absent installation look the same from here.';
  }
  return 'no repair applies to this state.';
}

async function get(path, token) {
  const headers = {
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(API + path, { headers });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return [res.status, body];
}

function flag(name) {
  const at = process.argv.indexOf(name);
  return (at === -1 || at === process.argv.length - 1) ? null : process.argv[at + 1];
}

async function main() {
  const target = splitRepo(flag('--repo'));
  if (!target) {
    console.error('pass --repo owner/name, or any GitHub URL for the repository');
    process.exitCode = 2;
    return;
  }
  const [owner, name] = target;

  const jwt = (process.env.GITHUB_APP_JWT || "dummy-github-app-jwt");
  if (!jwt) {
    console.error('set GITHUB_APP_JWT to the App JWT. The per-repository ' +
      'installation route is answered with the JWT, not with an installation ' +
      'access token');
    process.exitCode = 2;
    return;
  }

  // No credential on this one on purpose: whether the repository is publicly
  // readable is a fact about the world, not about the App.
  const [publicStatus, publicBody] = await get(`/repos/${owner}/${name}`);
  const [visState, visDetail] = visibility(publicStatus);
  console.log(`${visState}: ${visDetail}`);
  const ownerType = publicBody && publicBody.owner ? publicBody.owner.type : null;
  const repoCreated = publicBody ? parseIso(publicBody.created_at) : null;

  const [repoStatus, repoBody] = await get(`/repos/${owner}/${name}/installation`, jwt);
  console.log(`GET /repos/${owner}/${name}/installation returned ${repoStatus}`);

  const route = accountRoute(owner, ownerType);
  const [accountStatus, accountBody] = await get(route, jwt);
  console.log(`GET ${route} returned ${accountStatus}`);

  const [state, detail] = classify(repoStatus, accountStatus);
  console.log(`${state}: ${detail}`);

  const installation = repoStatus === 200 ? repoBody : accountBody;
  const selection = installation ? installation.repository_selection : null;
  const installationCreated = installation ? parseIso(installation.created_at) : null;
  const installationId = installation ? installation.id : null;
  if (installationId !== null && installationId !== undefined) {
    console.log(`installation ${installationId}, ` +
      `repository_selection=${selection ?? 'unknown'}`);
  }

  const [orderState, orderDetail] = creationOrder(repoCreated, installationCreated, selection);
  console.log(`${orderState}: ${orderDetail}`);
  console.log(`repair: ${repairFor(state, selection)}`);

  console.log(JSON.stringify({
    owner,
    repo: name,
    public_status: publicStatus,
    repo_installation_status: repoStatus,
    account_installation_status: accountStatus,
    account_route: route,
    repository_selection: selection ?? null,
    installation_id: installationId ?? null,
    visibility: visState,
    order: orderState,
    state,
  }, null, 2));
  process.exitCode = state === 'installed-on-this-repo' ? 0 : 1;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main() and set an exit code that fails a passing suite.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
