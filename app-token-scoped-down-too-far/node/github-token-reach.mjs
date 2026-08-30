/**
 * Say whether an installation access token was narrowed below what a job needs.
 *
 * Read only. One paginated GET for what the token reaches, one GET per
 * repository the job names. The token endpoint that would mint a wider token
 * is a write and is not called here: the script reads the token you already
 * hold and prints the mint request you should be making instead.
 *
 * A token cannot report its own permission map. The mint response echoed it
 * back to your own code and no read recovers it afterwards, so pass that saved
 * response as the third argument to make the permission half exact.
 *
 * Environment:
 *   GITHUB_INSTALLATION_TOKEN   the token the failing job holds
 */
import { readFile } from 'node:fs/promises';

const API = 'https://api.github.com';
const UA = 'github-token-reach/1.0';

export const RANK = { none: 0, read: 1, write: 2, admin: 3 };

/** A permission level as a comparable integer. Pure. */
export function rank(level) {
  const key = String(level ?? 'none').trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(RANK, key) ? RANK[key] : 0;
}

/** Turn contents:read,issues:write into a map. Pure. Bare names mean read. */
export function parseNeeds(spec) {
  const out = {};
  for (const chunk of String(spec ?? '').split(',')) {
    const item = chunk.trim();
    if (!item) continue;
    const at = item.indexOf(':');
    const name = (at === -1 ? item : item.slice(0, at)).trim().toLowerCase();
    const level = (at === -1 ? '' : item.slice(at + 1)).trim().toLowerCase();
    if (name) out[name] = level || 'read';
  }
  return out;
}

/** Read a saved mint response into the facts it carries. Pure. */
export function parseGrant(body) {
  const row = body && typeof body === 'object' ? body : {};
  const permissions = row.permissions && typeof row.permissions === 'object'
    ? row.permissions : null;
  const names = [];
  if (Array.isArray(row.repositories)) {
    for (const repo of row.repositories) {
      if (repo && typeof repo === 'object' && repo.full_name) names.push(String(repo.full_name));
      else if (typeof repo === 'string') names.push(repo);
    }
  }
  return {
    permissions,
    repository_selection: row.repository_selection ?? null,
    repositories: names,
  };
}

/** Needed repositories the token cannot reach. Pure. Case-insensitive. */
export function repoGap(reachable, needed) {
  const have = new Set((reachable ?? []).map((r) => String(r).trim().toLowerCase()));
  return (needed ?? []).filter((r) => !have.has(String(r).trim().toLowerCase()));
}

/**
 * Needed permissions the token holds at a lower level. Pure.
 * null when the grant was never seen, which is a blind spot and not a pass.
 */
export function permissionShortfall(granted, needed) {
  if (granted === null || granted === undefined) return null;
  const out = [];
  for (const name of Object.keys(needed ?? {}).sort()) {
    const wanted = needed[name];
    const have = granted[name];
    if (rank(have) < rank(wanted)) {
      out.push([name, String(wanted), have ? String(have) : 'absent']);
    }
  }
  return out;
}

/** Turn reach, grant and need into a finding. Pure. */
export function verdict(alive, missingRepos, shortfall, selection) {
  if (!alive) {
    return ['token-not-alive',
      'GET /installation/repositories did not return 200, so this is not a ' +
      'working installation access token and the narrowing question does not ' +
      'arise yet.'];
  }
  if (missingRepos && missingRepos.length) {
    return ['repos-out-of-reach',
      `${missingRepos.join(', ')} not in this token's repository set, so ` +
      'every call about them answers 404 whatever the App holds. Widen the ' +
      'repository list in the mint request.'];
  }
  if (shortfall === null || shortfall === undefined) {
    return ['narrowing-not-visible',
      'every repository the job needs is reachable. The permission half ' +
      'cannot be checked: a token does not report its own permission map, ' +
      'and no saved mint response was supplied.'];
  }
  if (shortfall.length) {
    return ['permissions-below-need',
      `${shortfall.map(([n, w, h]) => `${n} is ${h}, the job needs ${w}`).join('; ')}. ` +
      'The mint request asked for less than the job uses, which fails as 403 ' +
      'rather than as 404.'];
  }
  if (String(selection ?? '').trim().toLowerCase() === 'selected') {
    return ['narrowed-but-sufficient',
      'this token is narrowed to a repository subset and the subset still ' +
      'covers the job. Nothing to change.'];
  }
  return ['reach-covers-the-job',
    'this token reaches every repository the job needs and holds every ' +
    'permission at the level it asked for.'];
}

/** The change to make, in the mint request rather than in the App. Pure. */
export function repair(state, missingRepos, shortfall) {
  if (state === 'repos-out-of-reach') {
    return `add ${(missingRepos ?? []).join(', ')} to the repository list in ` +
      'the token request this job makes. If the installation already covers ' +
      'them, the App does not change at all.';
  }
  if (state === 'permissions-below-need') {
    return `raise ${(shortfall ?? []).map(([n, w]) => `${n} to ${w}`).join(', ')} ` +
      'in the permission map of the token request. If the installation does ' +
      'not hold it either, that is an App permission problem instead.';
  }
  if (state === 'narrowing-not-visible') {
    return 'keep the mint response your code already receives, with the token ' +
      'value stripped, and pass it back in. It is the only place the granted ' +
      'permission map is ever visible.';
  }
  return 'nothing. This token is not the constraint.';
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

async function reachableRepositories(token, pages = 10) {
  const names = [];
  let selection = null;
  let alive = false;
  for (let page = 1; page <= pages; page += 1) {
    const { status, body } = await get(token,
      `/installation/repositories?per_page=100&page=${page}`);
    if (status !== 200 || !body || typeof body !== 'object') {
      if (page === 1) {
        console.error(`GET /installation/repositories returned ${status}; ` +
          'only an installation access token can answer it');
      }
      break;
    }
    alive = true;
    selection = body.repository_selection ?? selection;
    const rows = body.repositories ?? [];
    for (const r of rows) if (r && r.full_name) names.push(String(r.full_name));
    if (rows.length < 100) break;
  }
  return { alive, names, selection };
}

async function main() {
  const token = (process.env.GITHUB_INSTALLATION_TOKEN || "dummy-github-installation-token") ?? (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_INSTALLATION_TOKEN to the token the failing job ' +
      'holds. The narrowing is a property of that token and of no other credential');
    process.exitCode = 2;
    return;
  }
  const needed = (process.argv[2] ?? '').split(',').map((s) => s.trim()).filter(Boolean);
  const needs = parseNeeds(process.argv[3] ?? '');
  const grantPath = process.argv[4] ?? null;

  const { alive, names, selection: seen } = await reachableRepositories(token);
  let selection = seen;
  if (alive) {
    console.log(`token reaches ${names.length} repository(ies), ` +
      `repository_selection=${selection ?? 'unreported'}`);
  }

  // Confirming per repository rather than trusting the list, which can be
  // truncated by a pagination cap the caller did not notice.
  const confirmed = [];
  for (const name of alive ? needed : []) {
    const { status } = await get(token, `/repos/${name}`);
    console.log(`GET /repos/${name} returned ${status}`);
    if (status === 200) confirmed.push(name);
  }
  const reachAll = [...new Set([...names, ...confirmed])].sort();

  let grant = { permissions: null, repository_selection: selection, repositories: [] };
  if (grantPath) {
    try {
      grant = parseGrant(JSON.parse(await readFile(grantPath, 'utf8')));
      selection = grant.repository_selection ?? selection;
    } catch (err) {
      console.error(`could not read the saved mint response: ${err.message}`);
    }
  }

  const missing = repoGap(reachAll, needed);
  const shortfall = permissionShortfall(grant.permissions, needs);
  const [state, detail] = verdict(alive, missing, shortfall, selection);
  console.log(`${state}: ${detail}`);
  console.log(`repair: ${repair(state, missing, shortfall)}`);

  console.log(JSON.stringify({
    reachable: reachAll,
    repository_selection: selection,
    needed_repositories: needed,
    missing_repositories: missing,
    permission_shortfall: shortfall,
    state,
  }, null, 2));
  process.exitCode = ['repos-out-of-reach', 'permissions-below-need'].includes(state) ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main(), fail on the missing token and set an exit code that
// fails the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
