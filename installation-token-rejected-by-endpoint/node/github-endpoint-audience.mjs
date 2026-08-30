/**
 * Say why a working GitHub App installation token is refused by one route.
 *
 * Read only. Two GETs: one that proves the token is alive, and one that
 * repeats the call that was already failing. The repair is a different
 * endpoint, and it is printed rather than applied.
 *
 * Credential classes, abbreviated throughout:
 *   s2s   an installation access token, acting as the App on one account
 *   u2s   a user access token, acting as a person who authorized the App
 *   jwt   the App's own JSON Web Token, signed with its private key
 *   any   any authenticated caller, a personal access token included
 *   none  no credential at all
 */
const API = 'https://api.github.com';
const UA = 'github-endpoint-audience/1.0';

/** Which credential classes each route template accepts. Curated, not fetched. */
export const AUDIENCES = {
  '/': ['none', 'any', 's2s', 'u2s', 'jwt'],
  '/meta': ['none', 'any', 's2s', 'u2s', 'jwt'],
  '/versions': ['none', 'any', 's2s', 'u2s', 'jwt'],
  '/rate_limit': ['any', 's2s', 'u2s', 'jwt'],
  '/app': ['jwt'],
  '/app/installations': ['jwt'],
  '/app/installations/{installation_id}': ['jwt'],
  '/installation/repositories': ['s2s'],
  '/user': ['any', 'u2s'],
  '/user/repos': ['any', 'u2s'],
  '/user/emails': ['any', 'u2s'],
  '/user/orgs': ['any', 'u2s'],
  '/user/keys': ['any', 'u2s'],
  '/user/installations': ['any', 'u2s'],
  '/notifications': ['any', 'u2s'],
  '/gists': ['any', 'u2s'],
  '/users/{username}': ['none', 'any', 's2s', 'u2s'],
  '/orgs/{org}': ['any', 's2s', 'u2s'],
  '/orgs/{org}/repos': ['any', 's2s', 'u2s'],
  '/orgs/{org}/members': ['any', 's2s', 'u2s'],
  '/repos/{owner}/{repo}': ['any', 's2s', 'u2s'],
  '/repos/{owner}/{repo}/issues': ['any', 's2s', 'u2s'],
  '/repos/{owner}/{repo}/pulls': ['any', 's2s', 'u2s'],
  '/repos/{owner}/{repo}/hooks': ['any', 's2s', 'u2s'],
  '/repos/{owner}/{repo}/commits': ['any', 's2s', 'u2s'],
  '/search/issues': ['any', 's2s', 'u2s'],
};

/** What to call instead. A null target means there is no s2s equivalent. */
export const SUBSTITUTES = {
  '/user': ['/app',
    'identifies the App itself, and is called with the App JWT rather than ' +
    'with the installation token'],
  '/user/repos': ['/installation/repositories',
    'returns exactly the repositories this installation covers, which is ' +
    'narrower and more accurate'],
  '/user/installations': ['/app/installations',
    'lists the installations of this App, under the App JWT'],
  '/user/orgs': ['/app/installations',
    'each installation names the account it sits on, which is the App ' +
    'equivalent of asking which orgs you are in'],
  '/user/emails': [null,
    'email addresses belong to a person; only a user access token can read them'],
  '/user/keys': [null,
    'SSH keys belong to a person; only a user access token can read them'],
  '/notifications': [null,
    'notifications belong to a person; subscribe the App to webhook events ' +
    'instead of polling a human inbox'],
  '/gists': [null, 'GitHub Apps cannot reach gists at all'],
  '/app': [null,
    'this route is right, but it wants the App JWT; the installation token is ' +
    'what the JWT produces, not a substitute for it'],
  '/app/installations': [null,
    'this route wants the App JWT, not the installation token it produces'],
};

/** Placeholder-free templates are matched first. */
const ROUTES = Object.keys(AUDIENCES).sort((a, b) => {
  const ca = (a.match(/\{/g) ?? []).length;
  const cb = (b.match(/\{/g) ?? []).length;
  return (ca - cb) || a.localeCompare(b);
});

/**
 * Reduce a request path to the route template it matches. Pure.
 * Full URLs, query strings, fragments and trailing slashes all land on the
 * same template. null when nothing matches.
 */
export function canonical(path) {
  let raw = String(path ?? '').split('?')[0].split('#')[0].trim();
  for (const prefix of ['https://api.github.com', 'http://api.github.com']) {
    if (raw.startsWith(prefix)) raw = raw.slice(prefix.length);
  }
  if (!raw.startsWith('/')) raw = `/${raw}`;
  const parts = raw.split('/').filter(Boolean);
  if (!parts.length) return '/';
  for (const template of ROUTES) {
    const segments = template.split('/').filter(Boolean);
    if (segments.length !== parts.length) continue;
    if (segments.every((s, i) => s.startsWith('{') || s === parts[i])) return template;
  }
  return null;
}

/** The credential classes a known route template accepts. Pure. */
export function accepts(route) {
  const found = AUDIENCES[route];
  return found ? new Set(found) : null;
}

/**
 * Heuristic audience for a path the table has never seen. Pure.
 * Returns [classes, reason]; classes is null where the heuristic declines.
 */
export function guess(path) {
  const parts = String(path ?? '').split('?')[0].split('/').filter(Boolean);
  if (!parts.length) return [null, 'an empty path matches nothing'];
  const head = parts[0];
  if (head === 'user') {
    return [new Set(['any', 'u2s']),
      'every route under /user means the authenticated user, and an ' +
      'installation is not a user'];
  }
  if (head === 'app') {
    return [new Set(['jwt']),
      'routes under /app identify the App and are signed with the App JWT ' +
      'rather than an installation token'];
  }
  if (head === 'installation') {
    return [new Set(['s2s']),
      "routes under /installation are the installation's own view of itself"];
  }
  if (head === 'notifications' || head === 'gists') {
    return [new Set(['any', 'u2s']),
      'this resource belongs to a person, so it needs a credential that has ' +
      'one behind it'];
  }
  return [null,
    'not in the table, and the first path segment carries no rule this ' +
    'script is willing to apply'];
}

/** The App-appropriate replacement for a route, if there is one. Pure. */
export function substitute(route) {
  return SUBSTITUTES[route] ?? null;
}

/** Turn a liveness proof and a route lookup into a finding. Pure. */
export function verdict(alive, status, route, classes, guessed = false) {
  if (!alive) {
    return ['not-an-installation-token',
      'GET /installation/repositories did not return 200, so this credential ' +
      'is not a live installation access token. Whatever the other call did, ' +
      'it is not the mismatch this script looks for.'];
  }
  if (status !== null && status !== undefined && status < 400) {
    return ['endpoint-accepted',
      `${route ?? 'that path'} returned ${status} with this installation ` +
      'token, so the route accepts it.'];
  }
  if (!classes) {
    return ['route-unknown',
      'this path is not in the route table and the heuristic declined it, so ' +
      'the audience is genuinely unknown. Check the published list of ' +
      'endpoints available to installation access tokens before rewriting ' +
      'anything.'];
  }

  const hedge = guessed ? ' (by heuristic rather than from the table)' : '';
  if (classes.has('s2s')) {
    return ['installation-tokens-accepted',
      `this route does accept installation access tokens${hedge}, so the ` +
      'refusal is about a permission rather than about the credential class. ' +
      'Read x-accepted-github-permissions on the same response.'];
  }
  if (classes.has('jwt') && !classes.has('u2s')) {
    return ['needs-app-jwt',
      `this route wants the App's own JWT${hedge}. The installation token is ` +
      'what the JWT produces, not a substitute for it: sign a fresh JWT and ' +
      'send that instead.'];
  }
  return ['needs-user-context',
    `this route accepts ${[...classes].sort().join(', ')}${hedge}. An ` +
    'installation access token is not one of them, so no permission opens ' +
    'it: the credential has no user behind it and the route is asking about one.'];
}

async function get(token, path) {
  const url = path.startsWith('/') ? API + path : path;
  const res = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': UA,
    },
  });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return { status: res.status, body };
}

async function main() {
  const token = (process.env.GITHUB_INSTALLATION_TOKEN || "dummy-github-installation-token") ?? (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_INSTALLATION_TOKEN to an installation access ' +
      'token. Without one there is no credential class to test a route against');
    process.exitCode = 2;
    return;
  }
  const path = process.argv[2] ?? '/user';

  const probe = await get(token, '/installation/repositories?per_page=1');
  const alive = probe.status === 200;
  if (alive) {
    const total = probe.body?.total_count;
    console.log('installation token alive: GET /installation/repositories ' +
      `returned 200 over ${total ?? 'an unreported number of'} repositories`);
  } else {
    console.log(`GET /installation/repositories returned ${probe.status}, so ` +
      'this is not a live installation access token');
  }

  let status = null;
  if (alive) {
    ({ status } = await get(token, path));
    console.log(`${path} returned ${status}`);
  }

  const route = canonical(path);
  let classes = route ? accepts(route) : null;
  let guessed = false;
  if (!classes) {
    const [guessed_classes, reason] = guess(path);
    classes = guessed_classes;
    guessed = Boolean(classes);
    console.log(`route: ${route ?? `not in the table (${reason})`}`);
  } else {
    console.log(`route: ${route} accepts ${[...classes].sort().join(', ')}`);
  }

  const [state, detail] = verdict(alive, status, route, classes, guessed);
  console.log(`${state}: ${detail}`);

  if (state === 'needs-user-context' || state === 'needs-app-jwt') {
    const swap = substitute(route);
    if (swap && swap[0]) {
      console.log(`repair: call ${swap[0]} instead, which ${swap[1]}`);
    } else if (swap) {
      console.log(`repair: there is no server-to-server equivalent. ${swap[1]}`);
    } else {
      console.log('repair: find the App equivalent of this route in the ' +
        'published endpoint list, or authorize a user and hold a user access ' +
        'token for them');
    }
  }
  if (state === 'installation-tokens-accepted') {
    console.log('repair: this is a permissions finding rather than a ' +
      "credential-class one; diff the App's permissions against the header " +
      'the failing response carried');
  }

  console.log(JSON.stringify({
    path,
    route,
    status,
    installation_token_alive: alive,
    accepts: classes ? [...classes].sort() : null,
    by_heuristic: guessed,
    state,
  }, null, 2));
  process.exitCode = (state === 'needs-user-context' ||
    state === 'needs-app-jwt') ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main(), fail on the missing token and set an exit code that
// fails the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
