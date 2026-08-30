/**
 * Name the GitHub App permission a 403 was actually asking for.
 *
 * Read only. GET requests and nothing else. The repair is printed, never
 * performed.
 */
const API = 'https://api.github.com';
const UA = 'github-app-permission-diff/1.0';

// Ordered so a comparison is arithmetic. "read" satisfying a "write" requirement
// is the most common way this error survives a careful look at a settings page.
const LEVELS = { none: 0, read: 1, write: 2, admin: 3 };

/**
 * Parse x-accepted-github-permissions into [permission, level] pairs. Pure.
 * Both commas and semicolons are accepted as separators rather than depending on
 * which one a given endpoint used.
 */
export function parseAccepted(value) {
  const raw = String(value ?? '').trim();
  if (!raw) return [];
  const out = [];
  for (const chunk of raw.replace(/;/g, ',').split(',')) {
    const at = chunk.indexOf('=');
    if (at < 0) continue;
    const name = chunk.slice(0, at).trim();
    const level = chunk.slice(at + 1).trim().toLowerCase();
    if (!name) continue;
    out.push([name, level]);
  }
  return out;
}

/**
 * Compare what the App holds against what the endpoint asked for. Pure.
 * `held` is the map from GET /app, or null when it could not be read.
 * Returns [state, detail].
 *
 * Where an endpoint lists alternatives, holding one can be enough, so reporting
 * every unmet pair is a superset: it may send you to check a permission you did
 * not need, but it never reports one as fine when it is not.
 */
export function diff(held, accepted, status = 403) {
  if (status < 400) {
    return ['accessible',
      `HTTP ${status}: the endpoint answered, so there is nothing to diff.`];
  }
  if (status !== 403) {
    return ['not-a-permission-error',
      `HTTP ${status} is not 'Resource not accessible by integration'. A 404 ` +
      'here is the masked-permission case and a 401 is a dead credential.'];
  }

  if (!accepted || accepted.length === 0) {
    return ['endpoint-refuses-apps',
      '403 with no x-accepted-github-permissions header. The endpoint does not ' +
      'accept an installation token at all, so no permission you add will open ' +
      "it: use the App equivalent, or a user-to-server token from the App's " +
      'OAuth flow.'];
  }

  const wanted = accepted.map(([n, l]) => `${n}: ${l}`).join(', ');

  if (held === null || held === undefined) {
    return ['needed',
      `the endpoint accepts ${wanted}. The App's own permission map is not ` +
      'readable with this credential; read it with GET /app under the App JWT ' +
      'to see which of those it is missing.'];
  }

  const missing = [];
  const low = [];
  for (const [name, level] of accepted) {
    const have = String(held[name] ?? 'none').trim().toLowerCase();
    const rank = LEVELS[have] ?? 0;
    const need = LEVELS[level] ?? 0;
    if (rank === 0) missing.push(`${name}: ${level}`);
    else if (rank < need) low.push(`${name} has ${have} and needs ${level}`);
  }

  if (missing.length === 0 && low.length === 0) {
    return ['sufficient',
      `the App already holds ${wanted}, so the permission map is not the cause. ` +
      'Check that the installation covers this repository and that the ' +
      'permission upgrade was accepted by this installation.'];
  }

  if (missing.length === 0) {
    return ['level-too-low',
      `held, but at the wrong level: ${low.join('; ')}. A permission at 'read' ` +
      'looks correct on a settings page and is not correct to the endpoint.'];
  }

  const extra = low.length ? ` Also at the wrong level: ${low.join('; ')}.` : '';
  return ['permission-absent', `not held at all: ${missing.join(', ')}.${extra}`];
}

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
}

async function get(token, url) {
  return fetch(url, { headers: headers(token) });
}

export async function heldPermissions(token, api = API) {
  const res = await get(token, `${api}/app`);
  if (res.status !== 200) return null;
  return (await res.json()).permissions ?? {};
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN (an App installation token, or the App JWT ' +
                  'if you also want the permission map)');
    process.exitCode = 2;
    return;
  }
  const at = process.argv.indexOf('--path');
  let path = at >= 0 ? process.argv[at + 1] : null;
  if (!path) {
    console.error('pass --path /repos/owner/name/pulls');
    process.exitCode = 2;
    return;
  }
  if (!path.startsWith('/')) path = `/${path}`;

  const probe = await get(token, API + path);
  const raw = probe.headers.get('x-accepted-github-permissions');
  console.log(`${path} -> HTTP ${probe.status}`);
  console.log(`x-accepted-github-permissions: ${raw ?? 'absent'}`);

  const accepted = parseAccepted(raw);
  const held = await heldPermissions(token);
  const [state, detail] = diff(held, accepted, probe.status);

  if (state === 'accessible') {
    console.log(`${state.padEnd(24)} ${detail}`);
    return;
  }

  console.warn(`${state.padEnd(24)} ${detail}`);
  if (held !== null) {
    const shown = Object.entries(held).sort()
      .map(([k, v]) => `${k}: ${v}`).join(', ');
    console.warn(`  the App holds: ${shown || 'nothing'}`);
  }
  if (state === 'permission-absent' || state === 'level-too-low') {
    console.warn('  repair: add exactly the permission named above to the App, ' +
                 'then have every installation owner accept the upgrade. Until an ' +
                 'installation accepts it, that installation keeps the old ' +
                 'permission set and keeps returning this same 403.');
  }
  process.exitCode = 1;
}

// Only run when invoked directly. The test file imports this module, and without
// the guard main() would run there too, fail on the missing token, and set a
// non-zero exit code that fails the whole test file even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
