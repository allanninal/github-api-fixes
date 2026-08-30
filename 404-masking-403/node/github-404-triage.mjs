/**
 * Tell apart the several different failures GitHub hides behind one 404.
 *
 * Read only. GET requests and nothing else: a token with read access is enough.
 * The repair is printed, never performed.
 */
const API = 'https://api.github.com';
const UA = 'github-404-triage/1.0';

// Longest prefixes first so a future prefix that extends an existing one cannot
// be swallowed by its shorter neighbour.
const PREFIXES = [
  ['github_pat_', 'fine-grained PAT'],
  ['ghp_', 'classic PAT'],
  ['gho_', 'OAuth user token'],
  ['ghu_', 'App user-to-server token'],
  ['ghs_', 'App installation token'],
  ['ghr_', 'App refresh token'],
];

/**
 * Name the credential from its prefix. Pure, and it never leaves the machine.
 */
export function tokenKind(token) {
  const value = String(token ?? '').trim();
  for (const [prefix, name] of PREFIXES) {
    if (value.startsWith(prefix)) return name;
  }
  return 'unknown';
}

/**
 * Read x-oauth-scopes into an array, keeping absent (null) and empty ([]) apart.
 * A classic token with nothing ticked sends an empty header; a fine-grained or
 * App token sends none at all, and those need opposite repairs.
 */
export function scopeList(headerValue) {
  if (headerValue === null || headerValue === undefined) return null;
  return headerValue.split(',').map((s) => s.trim()).filter(Boolean);
}

/**
 * Classify one 404. Pure, so the rules are readable rather than inferred.
 * Returns [state, detail].
 */
export function verdict(probe) {
  const status = probe.repo_status;

  if (!probe.authenticated) {
    return ['bad-credentials',
      'GET /user did not authenticate. Every private repository 404s for a dead ' +
      'token while every public one answers 200, which is why this looks like a ' +
      'per-repository permission problem.'];
  }

  if (status === 200) return ['visible', 'the repository answered 200'];
  if (status === 403) {
    return ['plain-403',
      '403 rather than 404, which is the honest one: rate limit, org IP allow ' +
      'list, or a policy that blocks this app. Read the message body and ' +
      'x-ratelimit-remaining before assuming access.'];
  }
  if (status !== 404) return ['unexpected', `HTTP ${status} is not the masked case`];

  if (probe.token_kind === 'App installation token') {
    const inside = probe.in_installation;
    if (inside === true) {
      return ['metadata-permission',
        'the repository is inside the installation, so it exists and you reach ' +
        'it. Every repository endpoint requires Metadata: Read; without it the ' +
        'repository itself 404s.'];
    }
    if (inside === false) {
      return ['not-in-installation',
        "the installation does not include this repository. repository_selection " +
        "is 'selected' and this one was never ticked, so it is outside the " +
        "token's world entirely."];
    }
    return ['installation-unknown',
      'GET /installation/repositories could not be read, so the installation ' +
      'question is open. Retry with the installation token the failing call ' +
      'actually uses.'];
  }

  const scopes = probe.scopes;
  if (scopes === null || scopes === undefined) {
    return ['repository-not-granted',
      'no x-oauth-scopes header, so this is a fine-grained token. Those grant ' +
      'repositories one at a time: this one is not in the token\'s repository ' +
      'list, or Metadata: Read is not on it.'];
  }
  if (!scopes.includes('repo')) {
    return ['missing-scope',
      `the token carries ${scopes.join(', ') || 'no scopes at all'} and not ` +
      "'repo'. Public repositories answer and private ones return exactly this 404."];
  }

  return ['no-access-or-gone',
    "the token authenticates and carries 'repo', so the scope is not the " +
    'problem. What is left is an account that was never granted access, or a ' +
    'repository that is genuinely gone. GitHub returns the same 404 for both on ' +
    'purpose and no header separates them.'];
}

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    // GitHub rejects requests with no User-Agent outright, which is its own
    // confusing 403 and not the one this script is about.
    'User-Agent': UA,
  };
}

async function get(token, url, params = {}) {
  const u = new URL(url);
  for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
  return fetch(u, { headers: headers(token) });
}

export async function installationRepos(token, api, limit = 2000) {
  const out = [];
  let page = 1;
  while (out.length < limit) {
    const res = await get(token, `${api}/installation/repositories`,
                          { per_page: 100, page });
    if (res.status !== 200) return null;
    const items = (await res.json()).repositories ?? [];
    out.push(...items);
    if (items.length < 100) break;
    page += 1;
  }
  return out;
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN (a read-only token is enough)');
    process.exitCode = 2;
    return;
  }
  const target = process.argv[2];
  if (!target || !target.includes('/')) {
    console.error('pass the repository as owner/name');
    process.exitCode = 2;
    return;
  }

  const kind = tokenKind(token);
  const me = await get(token, `${API}/user`);
  const probe = {
    token_kind: kind,
    authenticated: me.status === 200,
    scopes: scopeList(me.headers.get('x-oauth-scopes')),
    in_installation: null,
  };
  const login = probe.authenticated ? (await me.json()).login : null;

  const repo = await get(token, `${API}/repos/${target}`);
  probe.repo_status = repo.status;

  if (kind === 'App installation token' && repo.status === 404) {
    const repos = await installationRepos(token, API);
    if (repos !== null) {
      const full = target.toLowerCase();
      probe.in_installation = repos.some(
        (r) => String(r.full_name ?? '').toLowerCase() === full);
    }
  }

  const [state, detail] = verdict(probe);
  const line = `${state.padEnd(22)} ${target}  ${detail}`;
  if (state === 'visible') {
    console.log(`${line} (authenticated as ${login})`);
    return;
  }

  console.warn(line);
  console.warn(`  token: ${kind}, login: ${login}, scopes: ` +
    `${probe.scopes === null ? 'absent' : (probe.scopes.join(', ') || 'none')}`);
  const repairs = {
    'bad-credentials': 're-mint the token and assert GET /user returns the ' +
      'expected login at startup',
    'missing-scope': "re-create the classic token with the 'repo' scope, or move " +
      'to a fine-grained token listing this repository',
    'repository-not-granted': "add this repository to the fine-grained token's " +
      'repository access, with Metadata: Read',
    'not-in-installation': 'add the repository to the App installation, or switch ' +
      'the installation to All repositories',
    'metadata-permission': 'add Metadata: Read to the App and have each ' +
      'installation accept the updated permissions',
    'no-access-or-gone': `grant ${login} access to the repository, or confirm ` +
      'with somebody who can see it that it still exists',
  };
  if (repairs[state]) console.warn(`  repair: ${repairs[state]}`);
  process.exitCode = 1;
}

// Only run when invoked directly. The test file imports this module, and without
// the guard main() would run there too, fail on the missing token, and set a
// non-zero exit code that fails the whole test file even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
