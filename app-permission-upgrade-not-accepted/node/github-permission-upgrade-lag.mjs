/**
 * Find GitHub App installations still living on an older permission grant.
 *
 * Read only. Two GETs with the App's JWT: the App's own declaration and the
 * list of installations. Accepting a permission upgrade is a human act
 * performed by an account owner, so the script prints who has to be asked and
 * for what rather than doing anything itself.
 *
 * Environment:
 *   GITHUB_APP_JWT   the JWT your own signing code produced
 */
const API = 'https://api.github.com';
const UA = 'github-permission-upgrade-lag/1.0';

/** Permission levels are ordered, not a set. */
export const RANK = { none: 0, read: 1, write: 2, admin: 3 };

/** A permission level as a comparable integer. Pure. */
export function rank(level) {
  const key = String(level ?? 'none').trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(RANK, key) ? RANK[key] : 0;
}

/**
 * Declared permissions an installation holds at a lower level. Pure.
 * Returns [[permission, declaredLevel, grantedLevel], ...] sorted by name.
 */
export function permissionGap(declared, granted) {
  const out = [];
  for (const name of Object.keys(declared ?? {}).sort()) {
    const wanted = declared[name];
    const have = (granted ?? {})[name];
    if (rank(have) < rank(wanted)) {
      out.push([name, String(wanted), have ? String(have) : 'absent']);
    }
  }
  return out;
}

/** Permissions an installation holds beyond what the App declares. Pure. */
export function permissionSurplus(declared, granted) {
  const out = [];
  for (const name of Object.keys(granted ?? {}).sort()) {
    const have = granted[name];
    const wanted = (declared ?? {})[name];
    if (rank(have) > rank(wanted)) {
      out.push([name, wanted ? String(wanted) : 'not declared', String(have)]);
    }
  }
  return out;
}

/** Declared events an installation has not accepted. Pure. */
export function eventGap(declaredEvents, grantedEvents) {
  const have = new Set((grantedEvents ?? []).map((e) => String(e).trim().toLowerCase()));
  return [...new Set((declaredEvents ?? []).map((e) => String(e).trim().toLowerCase()))]
    .filter((e) => !have.has(e)).sort();
}

/** Sort one installation against the App declaration. Pure. */
export function classify(declaredPermissions, declaredEvents, inst) {
  const row = inst && typeof inst === 'object' ? inst : {};
  const account = row.account && typeof row.account === 'object' ? row.account : {};
  const gaps = permissionGap(declaredPermissions, row.permissions);
  const extra = permissionSurplus(declaredPermissions, row.permissions);
  const events = eventGap(declaredEvents, row.events);
  let state = 'current';
  if (gaps.length || events.length) state = 'upgrade-pending';
  else if (extra.length) state = 'grant-ahead';
  return {
    installation_id: row.id ?? null,
    account: account.login ?? null,
    state,
    permission_gap: gaps,
    permission_surplus: extra,
    event_gap: events,
  };
}

/** Turn the per-installation rows into one finding. Pure. */
export function verdict(rows) {
  const all = rows ?? [];
  if (!all.length) {
    return ['no-installations',
      'this App has no installations, so there is nothing to be behind. ' +
      'Nothing here is evidence about permissions.'];
  }
  const behind = all.filter((r) => r.state === 'upgrade-pending');
  if (behind.length) {
    return ['upgrades-pending',
      `${behind.length} of ${all.length} installation(s) are behind the App ` +
      'declaration. Their tokens carry the permission map they accepted, not ' +
      'the one the App settings page shows.'];
  }
  const ahead = all.filter((r) => r.state === 'grant-ahead');
  if (ahead.length) {
    return ['grants-ahead',
      `${ahead.length} of ${all.length} installation(s) hold more than the ` +
      'App declares, which happens after a permission is removed rather than ' +
      'added. Nothing is failing; the access is simply unused.'];
  }
  return ['all-current',
    `all ${all.length} installation(s) have accepted what the App declares.`];
}

/** Group the laggards by exactly what they are missing. Pure. */
export function cohorts(rows) {
  const out = new Map();
  for (const row of rows ?? []) {
    if (row.state !== 'upgrade-pending') continue;
    const key = row.permission_gap
      .map(([n, d, g]) => `${n} ${g} (declared ${d})`).join(', ') || 'events only';
    if (!out.has(key)) out.set(key, []);
    out.get(key).push(String(row.account ?? row.installation_id));
  }
  return Object.fromEntries([...out.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => [k, v.sort()]));
}

async function get(jwt, path) {
  const url = path.startsWith('/') ? API + path : path;
  const res = await fetch(url, {
    headers: {
      Authorization: `Bearer ${jwt}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': UA,
    },
  });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return { status: res.status, body };
}

async function listInstallations(jwt, pages = 10) {
  const out = [];
  for (let page = 1; page <= pages; page += 1) {
    const { status, body } = await get(jwt, `/app/installations?per_page=100&page=${page}`);
    if (status !== 200 || !Array.isArray(body)) {
      if (page === 1) {
        console.error(`GET /app/installations returned ${status}; this ` +
          'endpoint wants the App JWT');
      }
      break;
    }
    out.push(...body);
    if (body.length < 100) break;
  }
  return out;
}

async function main() {
  const jwt = (process.env.GITHUB_APP_JWT || "dummy-github-app-jwt");
  if (!jwt) {
    console.error('set GITHUB_APP_JWT to the JWT your own signing code ' +
      'produced. Both reads here are App-level and neither one accepts an ' +
      'installation token');
    process.exitCode = 2;
    return;
  }
  const only = process.argv[2] ?? null;

  const app = await get(jwt, '/app');
  if (app.status !== 200 || !app.body || typeof app.body !== 'object') {
    console.error(`GET /app returned ${app.status}, so there is no ` +
      'declaration to compare against');
    process.exitCode = 2;
    return;
  }
  const declaredPermissions = app.body.permissions ?? {};
  const declaredEvents = app.body.events ?? [];
  console.log(`app declares ${Object.keys(declaredPermissions).length} ` +
    `permission(s) and ${declaredEvents.length} event(s)`);

  const installations = await listInstallations(jwt);
  let rows = installations.map((i) => classify(declaredPermissions, declaredEvents, i));
  if (only) rows = rows.filter((r) => r.account === only);

  const [state, detail] = verdict(rows);
  console.log(`${state}: ${detail}`);
  for (const row of rows) {
    if (row.state === 'upgrade-pending') {
      for (const [name, want, have] of row.permission_gap) {
        console.log(`  ${row.installation_id} ${row.account}: ${name} ${have}, declared ${want}`);
      }
      if (row.event_gap.length) {
        console.log(`  ${row.installation_id} ${row.account}: events not ` +
          `accepted: ${row.event_gap.join(', ')}`);
      }
    }
    if (row.state === 'grant-ahead') {
      for (const [name, want, have] of row.permission_surplus) {
        console.log(`  ${row.installation_id} ${row.account} holds ${name} ${have}, ${want}`);
      }
    }
  }

  if (state === 'upgrades-pending') {
    console.log('repair: an owner on each account accepts the pending ' +
      "permission request from that org's Installed GitHub Apps page. Until " +
      "then, branch on the installation's own permission map rather than on " +
      'the App declaration');
  } else if (state === 'grants-ahead') {
    console.log('repair: nothing urgent. Those installations carry access the ' +
      'App no longer declares, which is a tidy-up rather than an outage');
  }

  console.log(JSON.stringify({
    declared_permissions: declaredPermissions,
    declared_events: [...declaredEvents].map(String).sort(),
    state,
    cohorts: cohorts(rows),
    installations: rows,
  }, null, 2));
  process.exitCode = state === 'upgrades-pending' ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main(), fail on the missing JWT and set an exit code that
// fails the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
