/**
 * Name the narrowest scope that would have made a refused GitHub call succeed.
 *
 * Read only. Both requests are GETs, and one of them is the call you are
 * already making. The repair is printed, never applied.
 *
 * x-oauth-scopes is what the token holds; x-accepted-oauth-scopes is what the
 * endpoint accepts, as alternatives. Held scopes imply narrower ones, so the
 * diff is computed rather than eyeballed.
 */
const API = 'https://api.github.com';
const UA = 'github-scope-diff/1.0';

/** Holding the key already grants everything in the value, transitively. */
export const IMPLIES = {
  repo: ['public_repo', 'repo:status', 'repo_deployment', 'repo:invite',
    'security_events'],
  'admin:org': ['write:org'],
  'write:org': ['read:org'],
  'admin:repo_hook': ['write:repo_hook'],
  'write:repo_hook': ['read:repo_hook'],
  'admin:org_hook': [],
  'admin:public_key': ['write:public_key'],
  'write:public_key': ['read:public_key'],
  'admin:gpg_key': ['write:gpg_key'],
  'write:gpg_key': ['read:gpg_key'],
  user: ['read:user', 'user:email', 'user:follow'],
  'write:packages': ['read:packages'],
  'write:discussion': ['read:discussion'],
  project: ['read:project'],
};

/** Lower is narrower. Only used to break ties between workable alternatives. */
export const RANK = {
  'read:org': 10, 'read:user': 10, 'read:packages': 10, 'read:project': 10,
  'read:discussion': 10, 'read:repo_hook': 10, 'repo:status': 12,
  'user:email': 12, repo_deployment: 15, security_events: 18,
  public_repo: 20, 'write:org': 30, 'write:repo_hook': 30,
  'write:packages': 30, 'write:discussion': 30, gist: 25, notifications: 25,
  'admin:repo_hook': 40, 'admin:org_hook': 45, workflow: 55, repo: 60,
  user: 60, 'admin:org': 70, delete_repo: 80, 'delete:packages': 80,
  site_admin: 95,
};
export const DEFAULT_RANK = 50;

/**
 * Parse an x-oauth-scopes header value. Pure.
 * null for an absent header, [] for a present but empty one: "does not use
 * scopes" and "was minted with none" are different findings.
 */
export function parseScopes(value) {
  if (value === null || value === undefined) return null;
  return String(value).split(',').map((s) => s.trim()).filter(Boolean);
}

/** Close a held scope set over the implication table. Pure. */
export function expand(scopes) {
  const seen = new Set();
  const queue = [...(scopes ?? [])];
  while (queue.length) {
    const scope = queue.pop();
    if (seen.has(scope)) continue;
    seen.add(scope);
    queue.push(...(IMPLIES[scope] ?? []));
  }
  return seen;
}

/**
 * Parse x-accepted-oauth-scopes into alternative requirement sets. Pure.
 * null for an absent header, [] for a present but empty one, which is the
 * endpoint saying it accepts any authenticated caller.
 */
export function alternatives(value) {
  if (value === null || value === undefined) return null;
  const out = [];
  for (const item of String(value).split(',')) {
    const parts = [...new Set(item.replace(/ and /g, ' ').split(/\s+/)
      .filter(Boolean))].sort();
    if (parts.length) out.push(parts);
  }
  return out;
}

/**
 * Decide whether held scopes satisfy an accepted list. Pure.
 * Returns [ok, options]; ok is null when the endpoint named no scopes.
 */
export function satisfies(held, accepted) {
  if (accepted === null || accepted === undefined) return [null, []];
  if (!accepted.length) return [true, []];
  const have = expand(held ?? []);
  const options = [];
  for (const alt of accepted) {
    const missing = alt.filter((s) => !have.has(s));
    if (!missing.length) return [true, []];
    options.push(missing);
  }
  const cost = (m) => m.reduce((n, s) => n + (RANK[s] ?? DEFAULT_RANK), 0);
  options.sort((a, b) => (a.length - b.length) || (cost(a) - cost(b)) ||
    a.join().localeCompare(b.join()));
  return [false, options];
}

/** Turn a status code and a header pair into a finding. Pure. */
export function verdict(status, held, accepted) {
  if (held === null || held === undefined) {
    return ['not-a-scoped-credential',
      'the response carried no x-oauth-scopes header, so this is a ' +
      'fine-grained token, an App installation token or no credential at all. ' +
      'None of those use scopes; they use per-resource permissions, and the ' +
      'missing one is named by x-accepted-github-permissions instead.'];
  }
  if (status < 400) {
    return ['call-succeeded',
      `the call returned ${status}, so there is nothing to diff. Held: ` +
      `${held.join(', ') || 'none'}`];
  }

  const [ok, options] = satisfies(held, accepted);
  if (ok === null) {
    return ['endpoint-named-no-scopes',
      `the ${status} response carried no x-accepted-oauth-scopes header, so ` +
      'the endpoint did not name a scope. Scope is not the cause here; look ' +
      'at SSO authorization, App installation coverage or plain lack of access.'];
  }
  if (ok && !accepted.length) {
    return ['any-token-accepted',
      'x-accepted-oauth-scopes was present and empty, which means the ' +
      `endpoint accepts any authenticated token. The ${status} is therefore ` +
      'not about scopes and no scope will fix it.'];
  }
  if (ok) {
    return ['scope-satisfied',
      `the token already satisfies ${accepted.map((a) => a.join('+')).join(' or ')}, ` +
      `so the ${status} has another cause. Held: ${held.join(', ') || 'none'}`];
  }
  const cheapest = options[0];
  return ['missing-scope',
    `add ${cheapest.join('+')} (narrowest of ${options.length} alternative(s)) ` +
    `and the call succeeds. Held: ${held.join(', ') || 'none'}. ` +
    `Accepted: ${accepted.map((a) => a.join('+')).join(' or ')}`];
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
  for (const [k, v] of res.headers.entries()) headers[k.toLowerCase()] = v;
  return { status: res.status, body, headers };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN. An anonymous request carries no ' +
      'x-oauth-scopes header at all, so there is nothing to diff');
    process.exitCode = 2;
    return;
  }
  const path = process.argv[2] ?? '/user';

  const base = await get(token, '/user');
  let held = parseScopes(base.headers['x-oauth-scopes'] ?? null);
  if (base.status === 200 && base.body) {
    console.log(`authenticated as ${base.body.login ?? 'an unnamed user'}`);
  } else if (base.status === 401) {
    console.error('GET /user returned 401, so the credential is rejected ' +
      'outright. That is a different problem from a narrow one');
    process.exitCode = 2;
    return;
  }

  const failing = await get(token, path);
  const onFailure = parseScopes(failing.headers['x-oauth-scopes'] ?? null);
  if (onFailure && onFailure.length) held = onFailure;
  const accepted = alternatives(failing.headers['x-accepted-oauth-scopes'] ?? null);

  console.log(`${path} returned ${failing.status}`);
  console.log(`held:     ${held === null ? 'header absent, not a scoped credential'
    : held.join(', ')}`);
  console.log(`accepted: ${failing.headers['x-accepted-oauth-scopes'] ?? 'header absent'}`);

  const [state, detail] = verdict(failing.status, held, accepted);
  console.log(`${state}: ${detail}`);

  if (state === 'missing-scope') {
    const [, options] = satisfies(held, accepted);
    console.log(`repair: mint a replacement token that adds ${options[0].join('+')}, ` +
      'deploy it, then revoke the old one. Scopes cannot be widened in place.');
    console.log('repair: for a gh CLI credential, gh auth refresh -h github.com ' +
      `-s ${options[0][0]}`);
  }
  if (state === 'not-a-scoped-credential') {
    console.log('repair: read x-accepted-github-permissions on the same ' +
      'response and add that permission to the App or the fine-grained token.');
  }

  console.log(JSON.stringify({ path, status: failing.status, held, accepted, state }, null, 2));
  process.exitCode = (state === 'missing-scope' ||
    state === 'not-a-scoped-credential') ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main(), fail on the missing token and set an exit code that
// fails the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
