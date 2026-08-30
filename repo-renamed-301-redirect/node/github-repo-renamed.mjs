/**
 * Find repositories whose configured name is a redirect to somewhere else.
 *
 * Read only. At most two GETs per repository: one with redirects disabled, and
 * one to follow the redirect where there is one. Nothing is written and the
 * repair is printed rather than performed.
 *
 * Environment:
 *   GITHUB_TOKEN     a token with read access to the repository
 *   GITHUB_REPOS     comma-separated owner/name values as your config has them
 *   GITHUB_CALLS     calls an hour your integration makes, optional
 */
const API = 'https://api.github.com';
const UA = 'github-repo-renamed/1.0';

/** 301 and 308 say the address changed. 302 and 307 say the routing changed. */
export const PERMANENT = [301, 308];
export const TEMPORARY = [302, 307];

const LOC_ID = /\/repositories\/(\d+)/;
const LOC_FULL = /\/repos\/([^/?#]+)\/([^/?#]+)/;

/** A finite number, or null. Pure. Number(null) is 0, which would lie here. */
function toNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** Whether this status moves you somewhere else. Pure. */
export function isRedirect(status) {
  const n = toNumber(status);
  return n !== null && (PERMANENT.includes(n) || TEMPORARY.includes(n));
}

/** Whether this status means update your code rather than follow it. Pure. */
export function isPermanent(status) {
  const n = toNumber(status);
  return n !== null && PERMANENT.includes(n);
}

/** What the Location header points at. Pure. Returns [kind, value] or null. */
export function repoFromLocation(location) {
  if (!location) return null;
  const text = String(location);
  const byId = LOC_ID.exec(text);
  if (byId) return ['id', byId[1]];
  const byName = LOC_FULL.exec(text);
  if (byName) return ['full_name', `${byName[1]}/${byName[2]}`];
  return null;
}

/** Whether two owner/name strings name the same repository. Pure. */
export function sameRepo(a, b) {
  if (!a || !b) return false;
  return String(a).trim().toLowerCase() === String(b).trim().toLowerCase();
}

/** Classify one probe of a configured repository name. Pure. */
export function verdict(asked, status, location = null, fullName = null) {
  const code = toNumber(status);
  if (code === null) return ['unknown', 'the probe produced no readable status.'];

  if (isPermanent(code)) {
    const target = repoFromLocation(location);
    if (!target) {
      return ['renamed-permanent',
        `${code} says the configured name is stale, but the response carried no `
        + 'usable Location, so the new name has to be read from the body after '
        + 'following it once.'];
    }
    const [kind, value] = target;
    const named = fullName ? `, now called ${fullName}` : '';
    if (kind === 'id') {
      return ['renamed-permanent',
        'the configured name is stale and GitHub is redirecting it permanently '
        + `to repository id ${value}${named}.`];
    }
    return ['renamed-permanent',
      'the configured name is stale and GitHub is redirecting it permanently to '
      + `${value}${named}.`];
  }

  if (isRedirect(code)) {
    return ['moved-temporary',
      `${code} is a temporary redirect, so follow it and change nothing. `
      + 'Writing this address into your configuration is the mistake here, not '
      + 'the fix.'];
  }

  if (code === 404) {
    return ['not-found',
      '404 is not a rename. It means no repository, no permission, no '
      + 'installation or a dead token, and separating those four is a different '
      + 'check.'];
  }

  if (code !== 200) {
    return ['unknown', `${code} is neither a redirect nor a readable repository.`];
  }

  if (!fullName) {
    return ['unknown',
      'the repository was returned without a full_name, so there is nothing to '
      + 'compare the configured name against.'];
  }

  if (String(asked).trim() === String(fullName).trim()) {
    return ['current',
      'the configured name matches full_name and the request was answered '
      + 'without a redirect.'];
  }

  if (sameRepo(asked, fullName)) {
    return ['case-only',
      `the configured name differs from ${fullName} only in capitalisation. `
      + 'GitHub matches names case-insensitively, so this is the same repository '
      + 'and there is nothing to do.'];
  }

  return ['renamed-followed',
    `the request was answered as ${fullName} rather than as the name that was `
    + 'asked for, so a redirect was followed somewhere between here and GitHub '
    + 'and nobody was told.'];
}

/** The identifiers that survive a rename. Pure. null when absent. */
export function durableKey(repo) {
  if (!repo || typeof repo !== 'object') return null;
  const key = {};
  for (const k of ['id', 'node_id']) {
    if (repo[k] !== undefined && repo[k] !== null) key[k] = repo[k];
  }
  return Object.keys(key).length ? key : null;
}

/** Requests a followed redirect adds over a period. Pure. */
export function extraRoundTrips(calls) {
  const n = toNumber(calls);
  if (n === null) return 0;
  return Math.max(0, Math.trunc(n));
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (state === 'renamed-permanent') {
    return 'update the stored owner/name to the value in the Location or in '
      + 'full_name, and key persistent state on the repository id or node_id, '
      + 'which survive the next rename too.';
  }
  if (state === 'renamed-followed') {
    return 'your client is following a redirect silently. Update the configured '
      + 'name to the full_name that came back, and key persistent state on id or '
      + 'node_id so the next rename is free.';
  }
  if (state === 'moved-temporary') {
    return 'follow it and change nothing. A temporary redirect is routing and '
      + 'does not belong in your configuration.';
  }
  if (state === 'case-only') {
    return 'nothing. The names differ only in capitalisation and GitHub matches '
      + 'them case-insensitively.';
  }
  if (state === 'not-found') {
    return 'triage the 404 rather than assuming a rename: check the token, the '
      + 'scopes and the installation before the name.';
  }
  if (state === 'current') return 'nothing.';
  return 'point the check at a repository this token can read.';
}

/** Requests this run will spend against the core quota, as an upper bound. Pure. */
export function readCost(repos) {
  return 2 * (Array.isArray(repos) ? repos.length : 0);
}

function headersFor(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const repos = ((process.env.GITHUB_REPO || "dummy-github-repo")S || '').split(',').map((s) => s.trim()).filter(Boolean);
  if (!token || repos.length === 0) {
    console.error('set GITHUB_TOKEN (read-only is enough) and GITHUB_REPOS=owner/name');
    process.exitCode = 2;
    return;
  }
  const calls = Number((process.env.GITHUB_CALL || "dummy-github-call")S || 0);
  console.log(`read cost: at most ${readCost(repos)} request(s) against the core hourly quota`);

  const findings = [];
  for (const name of repos) {
    // Manual, because with following enabled the client swallows the 301 and
    // hands back a 200 from an address nobody looked at.
    const res = await fetch(`${API}/repos/${name}`, {
      headers: headersFor(token),
      redirect: 'manual',
    });
    const location = res.headers.get('location');
    let repo = null;
    if (isRedirect(res.status) && location) {
      console.log(`${name}: ${res.status} -> ${location}`);
      const followed = await fetch(location, { headers: headersFor(token) });
      if (followed.ok) { try { repo = await followed.json(); } catch { repo = null; } }
    } else if (res.status === 200) {
      try { repo = await res.json(); } catch { repo = null; }
    }

    const fullName = repo ? repo.full_name : null;
    const [state, detail] = verdict(name, res.status, location, fullName);
    console.log(`${state}: ${detail}`);
    console.log(`repair: ${repair(state)}`);
    if (['renamed-permanent', 'renamed-followed'].includes(state) && calls) {
      console.log(`a client that follows this pays 1 extra request per call: `
        + `${calls} calls an hour becomes ${calls + extraRoundTrips(calls)}`);
    }

    findings.push({
      configured: name,
      status: res.status,
      location,
      location_points_at: repoFromLocation(location),
      full_name: fullName,
      durable_key: durableKey(repo),
      extra_requests_per_hour: ['renamed-permanent', 'renamed-followed'].includes(state)
        ? extraRoundTrips(calls) : 0,
      state,
      detail,
      repair: repair(state),
    });
  }

  console.log(JSON.stringify({ requests_spent_at_most: readCost(repos), findings }, null, 2));
  const bad = ['renamed-permanent', 'renamed-followed'];
  process.exitCode = findings.some((f) => bad.includes(f.state)) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
