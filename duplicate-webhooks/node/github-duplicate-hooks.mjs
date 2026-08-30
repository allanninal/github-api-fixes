/**
 * Find one webhook URL registered by more than one GitHub hook.
 *
 * Read only. Org hooks and repo hooks are independent objects, so the same URL
 * can be registered in both scopes and every overlapping event is delivered
 * twice. The script prints which hook to remove; it never removes one.
 */
const API = 'https://api.github.com';
const UA = 'github-duplicate-hooks/1.0';

// A hook subscribed to "*" receives every event type, so it intersects with
// anything the other hook on the same URL carries.
const WILDCARD = '*';

/**
 * Reduce a webhook URL to lowercase host plus path. Pure. Two hooks created
 * years apart differ cosmetically far more often than meaningfully, and a raw
 * string comparison across them reports a clean account.
 */
export function endpoint(url) {
  if (!url) return '';
  let parsed;
  try {
    parsed = new URL(String(url).trim());
  } catch {
    return String(url).trim().toLowerCase().replace(/\/+$/, '');
  }
  const host = parsed.hostname.toLowerCase();
  const port = (parsed.port && parsed.port !== '80' && parsed.port !== '443')
    ? `:${parsed.port}` : '';
  return host + port + parsed.pathname.replace(/\/+$/, '');
}

/** Events both hooks carry, sorted. Pure. A wildcard overlaps everything. */
export function overlap(a, b) {
  const sa = new Set(a ?? []);
  const sb = new Set(b ?? []);
  if (sa.has(WILDCARD) && sb.has(WILDCARD)) return [WILDCARD];
  if (sa.has(WILDCARD)) return [...sb].sort();
  if (sb.has(WILDCARD)) return [...sa].sort();
  return [...sa].filter((e) => sb.has(e)).sort();
}

/**
 * Group hooks by endpoint and classify each group. Pure. States: unique,
 * duplicate, latent, disjoint.
 */
export function group(hooks) {
  const byEndpoint = new Map();
  for (const h of hooks ?? []) {
    const key = endpoint(h.url);
    if (!byEndpoint.has(key)) byEndpoint.set(key, []);
    byEndpoint.get(key).push(h);
  }

  const rows = [];
  for (const target of [...byEndpoint.keys()].sort()) {
    const members = byEndpoint.get(target);
    const active = members.filter((m) => m.active !== false);
    const shared = [];
    for (let i = 0; i < active.length; i += 1) {
      for (let j = i + 1; j < active.length; j += 1) {
        for (const e of overlap(active[i].events, active[j].events)) {
          if (!shared.includes(e)) shared.push(e);
        }
      }
    }
    let state;
    if (members.length === 1) state = 'unique';
    else if (active.length < 2) state = 'latent';
    else if (shared.length) state = 'duplicate';
    else state = 'disjoint';
    rows.push({ endpoint: target, state, hooks: members, shared: shared.sort() });
  }
  return rows;
}

/**
 * Do the copies share a delivery guid? Pure. logs is {source: [delivery, ...]}
 * for one endpoint.
 */
export function guidPairs(logs) {
  const sourcesByGuid = new Map();
  const slots = new Map();
  for (const [source, deliveries] of Object.entries(logs ?? {})) {
    for (const d of deliveries ?? []) {
      if (d.guid) {
        if (!sourcesByGuid.has(d.guid)) sourcesByGuid.set(d.guid, new Set());
        sourcesByGuid.get(d.guid).add(source);
      }
      const when = String(d.delivered_at ?? '').slice(0, 16);
      if (when) {
        const key = `${d.event ?? ''}@${when}`;
        if (!slots.has(key)) slots.set(key, new Map());
        slots.get(key).set(source, d.guid);
      }
    }
  }
  let shared = 0;
  for (const sources of sourcesByGuid.values()) if (sources.size > 1) shared += 1;
  let twinned = 0;
  for (const seen of slots.values()) {
    if (seen.size > 1 && new Set(seen.values()).size > 1) twinned += 1;
  }
  return { shared_guids: shared, same_event_different_guid: twinned };
}

function nextLink(res) {
  for (const part of (res.headers.get('link') ?? '').split(',')) {
    const chunk = part.trim();
    if (chunk.startsWith('<') && chunk.endsWith('rel="next"')) {
      return chunk.slice(1, chunk.indexOf('>'));
    }
  }
  return null;
}

async function get(token, url) {
  const res = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': UA,
    },
  });
  if (res.status === 401) {
    throw new Error('401 from GitHub: GITHUB_TOKEN is missing, expired or malformed');
  }
  if (res.status === 403 || res.status === 404) {
    throw new Error(`${res.status} from ${url}: repository hooks need ` +
      'admin:repo_hook and organization hooks need admin:org_hook; GitHub ' +
      'answers 404 rather than 403 when the token cannot see the resource');
  }
  if (!res.ok) throw new Error(`${res.status} from ${url}`);
  return res;
}

async function page(token, url, limit = 500) {
  const out = [];
  let next = url;
  while (next && out.length < limit) {
    const res = await get(token, next);
    out.push(...(await res.json()));
    next = nextLink(res);
  }
  return out.slice(0, limit);
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN (a read-only token is enough)');
    process.exitCode = 2;
    return;
  }

  const scopes = [];
  for (const arg of process.argv.slice(2)) {
    if (arg.includes('/')) scopes.push([`repo ${arg}`, `${API}/repos/${arg}/hooks`]);
    else scopes.push([`org ${arg}`, `${API}/orgs/${arg}/hooks`]);
  }
  if (scopes.length === 0) {
    console.error('usage: node github-duplicate-hooks.mjs acme acme/api acme/web');
    process.exitCode = 2;
    return;
  }

  const hooks = [];
  for (const [source, base] of scopes) {
    for (const h of await page(token, `${base}?per_page=100`)) {
      hooks.push({ source, base, id: h.id, url: h.config?.url,
        events: h.events ?? [], active: h.active !== false });
    }
  }

  const rows = group(hooks);
  let duplicated = 0;
  let latent = 0;
  for (const row of rows) {
    const members = row.hooks
      .map((m) => `${m.source}#${m.id}${m.active ? '' : ' (inactive)'}`).join(', ');
    const line = `${row.state.padEnd(10)} ${row.endpoint || '?'}  ${members}`;
    if (row.state === 'unique' || row.state === 'disjoint') {
      console.log(line);
      if (row.state === 'disjoint') {
        console.log('  no shared events: a deliberate split, not a duplicate');
      }
      continue;
    }

    console.warn(line);
    if (row.state === 'latent') {
      latent += 1;
      const would = overlap(row.hooks[0].events, row.hooks[row.hooks.length - 1].events);
      console.warn('  only one hook is active. Re-enabling the other doubles ' +
        `delivery of: ${would.join(', ') || 'nothing'}`);
      continue;
    }

    duplicated += 1;
    console.warn(`  delivered twice: ${row.shared.join(', ')}`);
    const logs = {};
    for (const m of row.hooks) {
      logs[m.source] = await page(token,
        `${m.base}/${m.id}/deliveries?per_page=100`, 100);
    }
    const pairs = guidPairs(logs);
    console.warn(`  ${pairs.shared_guids} guid(s) seen under more than one hook, ` +
      `${pairs.same_event_different_guid} event(s) arriving twice under different guids`);
    if (pairs.same_event_different_guid) {
      console.warn('  deduplicating on X-GitHub-Delivery will not catch these; ' +
        'key the side effect on something in the payload instead');
    }
    console.warn('  repair: keep one source of truth and delete the other hook ' +
      'by hand (removal is not something this script will do)');
  }

  console.log(`${hooks.length} hook(s) across ${rows.length} endpoint(s), ` +
    `${duplicated} duplicated, ${latent} latent`);
  process.exitCode = duplicated ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not run main(), fail on the missing token, and fail the test file with it.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
