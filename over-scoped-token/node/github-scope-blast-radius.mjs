/**
 * Inventory what a working GitHub token is allowed to do that it never does.
 *
 * Read only, and in a stronger sense than usual: the single request is
 * GET /user. Nothing here probes a write to see whether it would be permitted,
 * because a probe that is permitted is a write.
 *
 * Nothing is failing when you run this. That is the point of it.
 */
const API = 'https://api.github.com';
const UA = 'github-scope-blast-radius/1.0';

/** What each scope authorizes, phrased as a verb rather than as a scope name. */
export const CAPABILITIES = {
  repo: ['push to every public and private repository the account can reach',
    'create and remove branches, tags and releases',
    'change repository settings, collaborators and deploy keys'],
  public_repo: ['push to every public repository the account can reach'],
  delete_repo: ['permanently remove any repository the account administers'],
  'admin:org': ['add and remove organization members',
    'create, rename and dissolve teams'],
  'write:org': ['change team membership and organization projects'],
  'admin:org_hook': ['create, edit and remove organization webhooks'],
  'admin:repo_hook': ['create, edit and remove repository webhooks'],
  'write:repo_hook': ['create and edit repository webhooks'],
  workflow: ['change workflow files, which run on the next push'],
  'write:packages': ['publish and overwrite package versions'],
  'delete:packages': ['permanently remove published package versions'],
  gist: ['create and edit gists on the account'],
  user: ['change the account profile and its email addresses'],
  'admin:public_key': ['add an SSH key to the account'],
  'admin:gpg_key': ['add a signing key to the account'],
  'write:discussion': ['post and edit team discussions'],
  notifications: ['mark notifications read and manage subscriptions'],
};

/** The smallest classic scope that serves each kind of read. */
export const NEEDS_CLASSIC = {
  'public-repos': [],
  'private-repos': ['repo'],
  'pull-requests': ['repo'],
  issues: ['repo'],
  'actions-runs': ['repo'],
  'org-members': ['read:org'],
  'repo-hooks': ['read:repo_hook'],
  packages: ['read:packages'],
  'user-profile': ['read:user'],
};

/** The same reads expressed as fine-grained permissions, which is the repair. */
export const NEEDS_FINE_GRAINED = {
  'public-repos': ['Metadata: Read'],
  'private-repos': ['Contents: Read', 'Metadata: Read'],
  'pull-requests': ['Metadata: Read', 'Pull requests: Read'],
  issues: ['Issues: Read', 'Metadata: Read'],
  'actions-runs': ['Actions: Read', 'Metadata: Read'],
  'org-members': ['Members: Read (organization)'],
  'repo-hooks': ['Webhooks: Read'],
  packages: ['Packages: Read'],
  'user-profile': ['Profile: Read (account)'],
};

/** Classic scopes that grant write and cannot be avoided for some reads. */
export const UNAVOIDABLY_BROAD = new Set(['repo', 'public_repo']);

/** Read x-oauth-scopes and say what kind of credential this is. Pure. */
export function heldScopes(headers) {
  const lowered = {};
  for (const [k, v] of Object.entries(headers ?? {})) lowered[String(k).toLowerCase()] = v;
  if (!('x-oauth-scopes' in lowered)) return [null, 'not-scope-based'];
  const raw = lowered['x-oauth-scopes'];
  return [String(raw).split(',').map((s) => s.trim()).filter(Boolean), 'scope-based'];
}

/** Minimum classic scopes and fine-grained permissions for declared reads. Pure. */
export function required(reads) {
  const classic = new Set();
  const fine = new Set();
  const unknown = [];
  for (const name of reads ?? []) {
    const key = String(name).trim().toLowerCase();
    if (!key) continue;
    if (!(key in NEEDS_CLASSIC)) { unknown.push(key); continue; }
    for (const s of NEEDS_CLASSIC[key]) classic.add(s);
    for (const p of NEEDS_FINE_GRAINED[key]) fine.add(p);
  }
  return {
    classic: [...classic].sort(),
    fine_grained: [...fine].sort(),
    unknown: unknown.sort(),
  };
}

/** Every write verb the given scopes authorize, deduplicated. Pure. */
export function capabilities(scopes) {
  const verbs = [];
  for (const scope of [...new Set(scopes ?? [])].sort()) {
    for (const verb of CAPABILITIES[scope] ?? []) {
      if (!verbs.includes(verb)) verbs.push(verb);
    }
  }
  return verbs;
}

/** Scopes held that no declared read asks for. Pure. */
export function excess(held, neededClassic) {
  const needed = new Set(neededClassic ?? []);
  return [...new Set(held ?? [])].filter((s) => !needed.has(s)).sort();
}

/** How many repositories the write verbs reach. Pure. */
export function blastRadius(user, held) {
  const writes = (held ?? []).filter((s) => s in CAPABILITIES);
  const body = (user && typeof user === 'object') ? user : {};
  let total = 0;
  let seenAny = false;
  for (const field of ['public_repos', 'total_private_repos']) {
    if (Number.isInteger(body[field])) { total += body[field]; seenAny = true; }
  }
  return {
    repositories: seenAny ? total : null,
    write_scopes: writes,
    verbs: capabilities(writes),
  };
}

/** Turn the inventory into a finding about a system that is working. Pure. */
export function verdict(kind, held, needed, radius) {
  if (kind === 'not-scope-based') {
    return ['not-scope-based',
      'no x-oauth-scopes header, so this credential carries per-repository ' +
      'permissions rather than account-wide scopes. There is nothing to narrow here.'];
  }

  const unnecessary = excess(held, needed.classic);
  const dangerous = unnecessary.filter((s) => s in CAPABILITIES);
  const reach = radius.repositories;
  const where = (reach === null || reach === undefined)
    ? 'every repository the account can reach' : `${reach} repositories`;

  if (dangerous.length) {
    return ['over-scoped',
      `${unnecessary.length} scope(s) held that no declared read needs, and ` +
      `${dangerous.length} of them grant write across ${where}: ${dangerous.join(', ')}`];
  }
  if (unnecessary.length) {
    return ['unused-scopes',
      `${unnecessary.length} scope(s) held that no declared read needs: ` +
      `${unnecessary.join(', ')}. None of them grant write, so this is untidy ` +
      'rather than dangerous.'];
  }
  if ((held ?? []).some((s) => UNAVOIDABLY_BROAD.has(s))) {
    return ['coarse-by-construction',
      'the scopes held are the minimum a classic token can have for these ' +
      `reads, and they still grant write across ${where}. No classic token is ` +
      'narrower than this one; the repair is a different credential type.'];
  }
  return ['least-privilege',
    'every scope held is required by a declared read, and none of them grant write.'];
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
  const headers = {};
  for (const [k, v] of res.headers.entries()) headers[k] = v;
  return { status: res.status, body, headers };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN to the credential you want inventoried');
    process.exitCode = 2;
    return;
  }
  const reads = (process.argv[2] ?? '').split(',').map((s) => s.trim()).filter(Boolean);

  const user = await get(token, '/user');
  if (user.status === 401) {
    console.error('GET /user returned 401. This credential does not ' +
      'authenticate, which is a different note');
    process.exitCode = 2;
    return;
  }
  if (user.status !== 200) {
    console.error(`GET /user returned ${user.status}; cannot inventory the credential`);
    process.exitCode = 2;
    return;
  }

  const [held, kind] = heldScopes(user.headers);
  const needed = required(reads);
  const radius = blastRadius(user.body, held);

  console.log(`authenticated as ${user.body?.login ?? 'an unnamed user'}`);
  console.log(`held:     ${held === null ? 'header absent' : (held.join(', ') || 'none')}`);
  console.log(`required: ${needed.classic.join(', ') || 'no scope at all for the declared reads'}`);
  if (needed.unknown.length) {
    console.warn(`unrecognised read(s) ${needed.unknown.join(', ')} were ignored; ` +
      'a typo here makes a token look broader than it is');
  }
  for (const verb of radius.verbs) console.warn(`this credential can ${verb}`);

  const [state, detail] = verdict(kind, held, needed, radius);
  console.log(`${state}: ${detail}`);

  if (state === 'over-scoped' || state === 'coarse-by-construction') {
    console.log('repair: mint a fine-grained token limited to the repositories ' +
      `this job reads, with exactly: ${needed.fine_grained.join(', ') || 'Metadata: Read'}`);
    console.log('repair: run both credentials side by side for one cycle, ' +
      'compare the output, then revoke the classic token.');
  }
  if (state === 'unused-scopes') {
    console.log(`repair: re-mint without ${excess(held, needed.classic).join(', ')}. ` +
      'Scopes cannot be removed from an existing classic token.');
  }
  console.log('note: a read-only token can only inventory itself. It cannot ' +
    'enumerate the other tokens on this account or say who else holds a copy.');

  console.log(JSON.stringify({
    kind, held, required: needed, blast_radius: radius, state,
  }, null, 2));
  process.exitCode = (state === 'least-privilege' || state === 'not-scope-based') ? 0 : 1;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main() and set an exit code that fails the suite.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
