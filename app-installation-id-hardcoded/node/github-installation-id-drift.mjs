/**
 * Find configured GitHub App installation ids that no longer mean what they did.
 *
 * Read only. One paginated GET over the App's own installations with the App
 * JWT, and one optional GET per configured account. The endpoint that mints an
 * installation access token is a write and is not called here.
 *
 * Environment:
 *   GITHUB_APP_JWT           the JWT your own signing code produced
 *   GITHUB_INSTALLATION_MAP  account=id pairs, comma separated, or a JSON object
 *   GITHUB_MAP_RECORDED_AT   optional ISO date the map was last written
 */
const API = 'https://api.github.com';
const UA = 'github-installation-id-drift/1.0';

/** The finding that never announces itself. */
export const SILENT = ['crossed'];

/** account=id pairs, or a JSON object, into a plain object. Pure. */
export function parseMap(text) {
  const raw = String(text ?? '').trim();
  if (!raw) return {};
  if (raw.startsWith('{')) {
    let loaded;
    try { loaded = JSON.parse(raw); } catch { return {}; }
    const out = {};
    for (const [k, v] of Object.entries(loaded || {})) {
      const key = String(k).trim().toLowerCase();
      if (key) out[key] = String(v).trim();
    }
    return out;
  }
  const out = {};
  for (const chunk of raw.replace(/;/g, ',').split(',')) {
    const at = chunk.indexOf('=');
    if (at < 0) continue;
    const account = chunk.slice(0, at).trim().toLowerCase();
    const ident = chunk.slice(at + 1).trim();
    if (account && ident) out[account] = ident;
  }
  return out;
}

/** The login of the account an installation sits on. Pure. */
export function accountOf(inst) {
  if (!inst || typeof inst !== 'object') return null;
  const account = inst.account;
  if (account && typeof account === 'object' && account.login) return String(account.login);
  return null;
}

/** The value worth keying stored state on. Pure. */
export function stableKey(inst) {
  const login = accountOf(inst);
  return login ? login.toLowerCase() : null;
}

/** Installations by their id, as text. Pure. */
export function indexById(installations) {
  const out = {};
  for (const inst of installations || []) {
    if (inst && typeof inst === 'object' && inst.id !== null && inst.id !== undefined) {
      out[String(inst.id).trim()] = inst;
    }
  }
  return out;
}

/** Installations by lowercased account login. Pure. */
export function indexByAccount(installations) {
  const out = {};
  for (const inst of installations || []) {
    const key = stableKey(inst);
    if (key) out[key] = inst;
  }
  return out;
}

/** The id this account's installation has right now, or null. Pure. */
export function currentIdFor(account, byAccount) {
  const inst = (byAccount || {})[String(account ?? '').trim().toLowerCase()];
  if (!inst || typeof inst !== 'object') return null;
  return inst.id === null || inst.id === undefined ? null : String(inst.id);
}

/** An ISO 8601 timestamp as epoch milliseconds, or null. Pure. */
export function parseMoment(text) {
  const raw = String(text ?? '').trim();
  if (!raw) return null;
  const ms = Date.parse(raw);
  return Number.isNaN(ms) ? null : ms;
}

/** Whether this installation was created after the map was written. Pure. */
export function reinstalledSince(inst, recordedAt) {
  const created = parseMoment(inst && typeof inst === 'object' ? inst.created_at : null);
  const recorded = parseMoment(recordedAt);
  if (created === null || recorded === null) return null;
  return created > recorded;
}

/** Compare one configured pair against reality. Pure. */
export function drift(account, configuredId, byId, byAccount, recordedAt = null) {
  const name = String(account ?? '').trim();
  const wanted = String(configuredId ?? '').trim();
  const listed = (byId || {})[wanted];
  const current = currentIdFor(name, byAccount);

  if (listed !== undefined && listed !== null) {
    const owner = accountOf(listed) || '';
    if (owner.toLowerCase() !== name.toLowerCase()) {
      return ['crossed',
        `${name} is configured as ${wanted}, which exists and belongs to `
        + `${owner || 'another account'}. Nothing about this fails: it works `
        + 'against the wrong account.'];
    }
    if (reinstalledSince(listed, recordedAt)) {
      return ['current-but-reinstalled',
        `${name} still resolves to ${wanted}, and that installation was created `
        + 'after the map was written, so the App was removed and re-added at '
        + 'some point.'];
    }
    return ['current', `${name} resolves to ${wanted}.`];
  }

  if (current !== null) {
    const created = ((byAccount || {})[name.toLowerCase()] || {}).created_at;
    return ['stale',
      `${name} is configured as ${wanted}, which this App no longer has. The `
      + `current installation for ${name} is ${current}`
      + `${created ? `, created ${created}` : ''}.`];
  }
  return ['gone',
    `${name} is configured as ${wanted} and this App has no installation on `
    + 'that account at all. It was uninstalled and not put back.'];
}

/** Accounts the App is installed on that the configuration omits. Pure. */
export function unmapped(byAccount, configured) {
  const known = new Set(Object.keys(configured || {}).map((k) => String(k).trim().toLowerCase()));
  return Object.keys(byAccount || {}).filter((k) => !known.has(k)).sort();
}

/** Counts by state, with the silent finding pulled out. Pure. */
export function summarize(findings) {
  const counts = {};
  for (const f of findings || []) counts[f.state] = (counts[f.state] || 0) + 1;
  return {
    total: (findings || []).length,
    by_state: counts,
    silent: SILENT.reduce((n, s) => n + (counts[s] || 0), 0),
  };
}

/** The sentence a reader has to act on. Pure. */
export function repair(state, account = null, current = null) {
  if (state === 'crossed') {
    return `stop the deploy. The id filed under ${account || 'this account'} `
      + 'belongs to another account, so every call made with it lands on the '
      + 'wrong organization and nothing will ever error. Fix the mapping, then '
      + 'resolve the id at runtime so it cannot drift again.';
  }
  if (state === 'stale') {
    return 'resolve the id per account from the org\'s own installation route '
      + `rather than storing it. The current id is ${current || 'on the list above'} `
      + 'today and will be a different one after the next reinstall.';
  }
  if (state === 'gone') {
    return `the App is not installed on ${account || 'that account'}. This is `
      + 'not an id problem: somebody has to install it again, and your code '
      + 'should key state on the account login so the history survives.';
  }
  if (state === 'current-but-reinstalled') {
    return 'nothing is broken, but the id changed hands once already. Move the '
      + 'lookup into the code before it changes again.';
  }
  return 'nothing. This account resolves correctly.';
}

function headers(jwt) {
  return {
    Authorization: `Bearer ${jwt}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
}

async function get(jwt, path) {
  const res = await fetch(API + path, { headers: headers(jwt) });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return { status: res.status, body };
}

async function main() {
  const jwt = (process.env.GITHUB_APP_JWT || "dummy-github-app-jwt");
  if (!jwt) {
    console.error('set GITHUB_APP_JWT; the installation list wants the App JWT');
    process.exitCode = 2;
    return;
  }
  const configured = parseMap((process.env.GITHUB_INSTALLATION_MA || "dummy-github-installation-ma")P || '');
  if (!Object.keys(configured).length) {
    console.error('set GITHUB_INSTALLATION_MAP to account=id pairs');
    process.exitCode = 2;
    return;
  }
  const recordedAt = (process.env.GITHUB_MAP_RECORDED_A || "dummy-github-map-recorded-a")T || null;

  const installations = [];
  for (let page = 1; page <= 10; page += 1) {
    const { status, body } = await get(jwt, `/app/installations?per_page=100&page=${page}`);
    if (status !== 200 || !Array.isArray(body)) {
      if (page === 1) console.error(`GET /app/installations returned ${status}`);
      break;
    }
    installations.push(...body);
    if (body.length < 100) break;
  }
  console.log(`${installations.length} installation(s) visible to this App`);

  const byId = indexById(installations);
  const byAccount = indexByAccount(installations);
  const findings = Object.entries(configured).sort().map(([account, ident]) => {
    const [state, detail] = drift(account, ident, byId, byAccount, recordedAt);
    return {
      account, configured_id: ident, state, detail,
      current_id: currentIdFor(account, byAccount),
    };
  });
  findings.sort((a, b) => (SILENT.includes(b.state) ? 1 : 0) - (SILENT.includes(a.state) ? 1 : 0)
    || a.account.localeCompare(b.account));

  for (const f of findings) {
    if (f.state !== 'current') {
      console.log(`${f.state}: ${f.detail}`);
      console.log(`repair: ${repair(f.state, f.account, f.current_id)}`);
    }
  }
  const extra = unmapped(byAccount, configured);
  if (extra.length) console.log(`also installed and not in the map: ${extra.join(', ')}`);

  const stats = summarize(findings);
  console.log(JSON.stringify({
    visible: installations.length, summary: stats, unmapped_accounts: extra, findings,
  }, null, 2));
  process.exitCode = (stats.by_state.current || 0) === stats.total ? 0 : 1;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
