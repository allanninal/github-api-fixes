/**
 * Find GitHub webhooks with no secret, and hooks whose secret is being rejected.
 *
 * Read only. The script can prove a hook has no secret, because the key is
 * absent from config. It cannot prove a secret is correct: the value comes back
 * masked, so a wrong secret and a right one are indistinguishable until
 * deliveries start failing.
 */
const API = 'https://api.github.com';
const UA = 'github-hook-secret-audit/1.0';

// What GitHub returns in place of a secret that is set. Its presence is the only
// positive signal available; its value carries no information at all.
const MASK = '********';

/**
 * Is a secret configured on this hook? Pure. GitHub masks a configured secret
 * and omits the key when there is none, so absence is a real finding.
 */
export function secretState(hook) {
  const config = hook.config;
  if (config === null || typeof config !== 'object') return 'unknown';
  if (!Object.prototype.hasOwnProperty.call(config, 'secret')) return 'absent';
  const value = config.secret;
  if (value === null || value === undefined || String(value).trim() === '') {
    return 'absent';
  }
  return 'set';
}

/**
 * Count deliveries the receiver refused with 401 or 403. Pure. On a hook that
 * has a secret these are the only visible trace of a mismatch.
 */
export function unauthorized(deliveries) {
  let rejected = 0;
  let total = 0;
  for (const d of deliveries ?? []) {
    total += 1;
    const code = Number.parseInt(d.status_code, 10);
    if (code === 401 || code === 403) rejected += 1;
  }
  return { rejected, total };
}

/**
 * Classify one hook. Pure. "unsigned" is a fact about the configuration;
 * "signed" is the absence of evidence and says so.
 */
export function verdict(hook, rejected = 0, delivered = 0) {
  const state = secretState(hook);
  const url = hook.config?.url ?? 'the configured URL';

  if (state === 'unknown') {
    return ['unknown', 'no config on this hook, which should not happen; ' +
      're-read it with GET /repos/{owner}/{repo}/hooks/{id}'];
  }

  if (state === 'absent') {
    return ['unsigned',
      'config has no secret key, so GitHub sends no X-Hub-Signature-256 header ' +
      'with these payloads. A receiver that verifies only when the header is ' +
      `present verifies nothing, and anyone who learns ${url} can post to it.`];
  }

  if (rejected && delivered && rejected * 2 >= delivered) {
    return ['rejected',
      `a secret is set and ${rejected} of ${delivered} recent deliveries came ` +
      'back 401 or 403 from your server. That is what a mismatched secret looks ' +
      'like from here; the value itself is masked and cannot be compared.'];
  }

  let detail = 'a secret is set, so payloads are signed. The value is masked as ' +
    `${MASK}, so this says nothing about whether it matches the one your ` +
    'receiver holds.';
  if (rejected) {
    detail += ` ${rejected} of ${delivered} recent deliveries were refused with ` +
      '401 or 403, which is worth reading before you trust it.';
  }
  return ['signed', detail];
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
    throw new Error(`${res.status} from ${url}: listing hooks needs ` +
      'admin:repo_hook for a repository or admin:org_hook for an organization');
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
    console.error('usage: node github-hook-secret-audit.mjs owner/name [org ...]');
    process.exitCode = 2;
    return;
  }

  let unsigned = 0;
  let refusing = 0;
  let total = 0;
  for (const [label, base] of scopes) {
    for (const hook of await page(token, `${base}?per_page=100`)) {
      total += 1;
      const deliveries = await page(token,
        `${base}/${hook.id}/deliveries?per_page=100`, 50);
      const { rejected, total: delivered } = unauthorized(deliveries);
      const [state, detail] = verdict(hook, rejected, delivered);
      const url = hook.config?.url ?? '?';
      const line = `${state.padEnd(8)} ${label} ${url}  ${detail}`;
      if (state === 'signed') { console.log(line); continue; }
      console.warn(line);
      if (state === 'unsigned') {
        unsigned += 1;
        console.warn('  repair: set a high-entropy secret on this hook, then ' +
          'make the receiver reject any request without X-Hub-Signature-256 ' +
          'rather than skipping the check');
      } else if (state === 'rejected') {
        refusing += 1;
        console.warn("  repair: compare the secret in your receiver's " +
          'environment with the one on the hook, then replay with POST ' +
          `${base}/${hook.id}/deliveries/{delivery_id}/attempts`);
      }
    }
  }

  console.log(`${total} hook(s), ${unsigned} unsigned, ${refusing} rejecting deliveries`);
  process.exitCode = (unsigned || refusing) ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not run main(), fail on the missing token, and fail the test file with it.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
