/**
 * Find webhooks GitHub delivers to without checking the TLS certificate.
 *
 * Read only. Every call is a GET. Changing the flag is a write and is not done
 * here: the script prints the request, as a full config rather than a single
 * field, because a webhook's config is replaced rather than merged and the
 * secret you read back is a mask.
 *
 * config.insecure_ssl set to "1" tells GitHub to skip verification of the
 * endpoint's certificate. The connection is still TLS; what is lost is the
 * guarantee that the endpoint is yours.
 *
 * The secret is never printed. Its presence is read only to decide whether the
 * repair needs to mention rotation.
 *
 * Environment:
 *   GITHUB_TOKEN   a read-only token that can see the repository's hooks
 *
 * Usage:
 *   node github-hook-ssl-verification.mjs acme-corp/api @acme-corp
 */
const API = 'https://api.github.com';
const UA = 'github-hook-ssl-verification/1.0';

const INSECURE_ON = ['1', 'true', 'yes', 'on'];
const INSECURE_OFF = ['0', 'false', 'no', 'off'];

/** The config object of a hook, or an empty object. Pure. */
export function configOf(hook) {
  if (!hook || typeof hook !== 'object') return {};
  const config = hook.config;
  return config && typeof config === 'object' ? config : {};
}

/**
 * Three-state read of insecure_ssl: on, off or unknown. Pure.
 * Both '0' and '1' are non-empty strings, so a truthy test reports every
 * correctly configured hook as insecure.
 */
export function insecureFlag(hook) {
  const config = configOf(hook);
  if (!('insecure_ssl' in config)) return 'unknown';
  const raw = config.insecure_ssl;
  if (typeof raw === 'boolean') return raw ? 'on' : 'off';
  if (typeof raw === 'number') return raw ? 'on' : 'off';
  const text = String(raw ?? '').trim().toLowerCase();
  if (INSECURE_ON.includes(text)) return 'on';
  if (INSECURE_OFF.includes(text)) return 'off';
  return 'unknown';
}

/** The URL scheme of a hook, lower-cased, or '' when there is none. Pure. */
export function schemeOf(hook) {
  const url = String(configOf(hook).url ?? '').trim();
  if (!url.includes('://')) return '';
  return url.split('://')[0].toLowerCase();
}

/** The hook's URL with any query string dropped. Pure. */
export function endpoint(hook) {
  const url = String(configOf(hook).url ?? '').trim();
  return url ? url.split('?')[0] : 'an unset URL';
}

/** Whether the hook has a secret set. Pure. The value is never read. */
export function hasSecret(hook) {
  return 'secret' in configOf(hook);
}

/** An ISO 8601 timestamp as epoch milliseconds, or null. Pure. */
export function parsedTime(text) {
  const raw = String(text ?? '').trim();
  if (!raw || ['null', 'none'].includes(raw.toLowerCase())) return null;
  const ms = Date.parse(raw);
  return Number.isNaN(ms) ? null : ms;
}

/** Days since the hook config was last edited, or null. Pure. A lower bound. */
export function unchangedDays(hook, nowMs) {
  if (!hook || typeof hook !== 'object' || nowMs === null || nowMs === undefined) {
    return null;
  }
  const when = parsedTime(hook.updated_at);
  if (when === null) return null;
  return Math.floor((nowMs - when) / 86400000);
}

/** Sort one hook into a state and a sentence. Pure. */
export function classify(hook, nowMs = null) {
  const ident = `hook ${(hook && typeof hook === 'object' ? hook.id : null) ?? '?'}`;
  const scheme = schemeOf(hook);
  const flag = insecureFlag(hook);
  if (!scheme) {
    return ['no-url',
      `${ident} has no usable URL in its config, so there is nothing to ` +
      'verify a certificate against.'];
  }
  if (scheme !== 'https') {
    return ['not-applicable',
      `${ident} posts to a ${scheme}:// URL, so no certificate is checked ` +
      'because no TLS handshake happens. insecure_ssl is not the finding ' +
      'here; the scheme is.'];
  }
  if (flag === 'on') {
    const age = unchangedDays(hook, nowMs);
    return ['verification-off',
      `${ident} posts to ${endpoint(hook)} with certificate verification ` +
      `disabled${age !== null ? `, and has not been edited for at least ${age} day(s)` : ''}. ` +
      'Deliveries succeed, so nothing else reports this.'];
  }
  if (flag === 'unknown') {
    return ['flag-unreadable',
      `${ident} does not report a readable insecure_ssl value. Read it in the ` +
      "hook's settings rather than assuming either answer."];
  }
  return ['verified',
    `${ident} posts to ${endpoint(hook)} and GitHub checks the certificate.`];
}

/** The change to make, printed as a whole config. Pure. */
export function repair(state, hook) {
  if (state === 'verification-off') {
    const rotate = hasSecret(hook)
      ? ' and a new secret'
      : ' and a secret, since this hook has none';
    return 'install a certificate that chains to a public root, confirm it ' +
      "from outside your network, then send the hook's full config with " +
      `insecure_ssl "0"${rotate}. The config is replaced, not merged, and the ` +
      'secret you read back is a mask.';
  }
  if (state === 'not-applicable') {
    return 'move the receiver behind HTTPS and change the URL. Until then ' +
      'insecure_ssl is a field about a handshake this hook never performs.';
  }
  if (state === 'flag-unreadable') {
    return "open the hook's settings and read the SSL verification setting by hand.";
  }
  if (state === 'no-url') {
    return 'set a URL on this hook, or delete it. A hook with no endpoint ' +
      'delivers nothing and hides in every audit that counts hooks.';
  }
  return "nothing. GitHub verifies this endpoint's certificate.";
}

/** Counts across every hook read. Pure. */
export function summarize(hooks, nowMs = null) {
  const rows = (hooks ?? []).filter((h) => h && typeof h === 'object');
  const states = rows.map((h) => classify(h, nowMs)[0]);
  const count = (name) => states.filter((s) => s === name).length;
  return {
    total: rows.length,
    verification_off: count('verification-off'),
    verified: count('verified'),
    plaintext: count('not-applicable'),
    unreadable: count('flag-unreadable') + count('no-url'),
  };
}

async function get(token, path) {
  const res = await fetch(path.startsWith('/') ? API + path : path, {
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

async function listHooks(token, scope) {
  const path = scope.startsWith('@')
    ? `/orgs/${scope.slice(1)}/hooks?per_page=100`
    : `/repos/${scope}/hooks?per_page=100`;
  const { status, body } = await get(token, path);
  if (status !== 200 || !Array.isArray(body)) {
    console.error(`GET ${path} returned ${status}`);
    return [];
  }
  return body;
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN to a read-only token that can see the ' +
      "repository's hooks");
    process.exitCode = 2;
    return;
  }
  const scopes = process.argv.slice(2);
  if (scopes.length === 0) {
    console.error('pass at least one owner/name, or @org for an organization');
    process.exitCode = 2;
    return;
  }

  const now = Date.now();
  const findings = [];
  for (const scope of scopes) {
    const label = scope.startsWith('@') ? scope.slice(1) : scope;
    const hooks = await listHooks(token, scope);
    const stats = summarize(hooks, now);
    console.log(`${stats.total} hook(s) on ${label}`);
    for (const hook of hooks) {
      const [state, detail] = classify(hook, now);
      findings.push({
        scope: label,
        hook_id: hook.id,
        state,
        detail,
        url: endpoint(hook),
        secret_set: hasSecret(hook),
      });
      if (state !== 'verified') {
        console.log(`${state}: ${detail}`);
        console.log(`repair: ${repair(state, hook)}`);
      }
    }
    if (stats.verification_off === 0) {
      console.log(`verified: no hook on ${label} has certificate verification disabled`);
    }
  }

  console.log(JSON.stringify({ scopes, findings }, null, 2));
  process.exitCode = findings.some((f) => f.state === 'verification-off') ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main() and set an exit code that fails the suite even as
// every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
