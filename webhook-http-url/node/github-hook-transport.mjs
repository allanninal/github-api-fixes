/**
 * Find webhooks that deliver over plaintext HTTP, and say which ones leak.
 *
 * Read only. Every call is a GET. Changing a hook's URL is a write and is not
 * done here: the script prints the change, as a full config rather than a
 * single field, because a webhook's config is replaced rather than merged and
 * the secret you read back is a mask.
 *
 * Two things that look the same in this field are kept apart: http:// on a
 * routable host means payloads are readable in transit, while http:// on a
 * private address means GitHub cannot route there at all and the hook has
 * never delivered anything.
 *
 * The secret is never printed, and neither is any query string or userinfo in
 * a hook URL.
 *
 * Environment:
 *   GITHUB_TOKEN   a read-only token that can see the repository's hooks
 *
 * Usage:
 *   node github-hook-transport.mjs acme-corp/api @acme-corp
 */
const API = 'https://api.github.com';
const UA = 'github-hook-transport/1.0';

const LOCAL_NAMES = ['localhost', 'localhost.localdomain', 'ip6-localhost'];
const LOCAL_SUFFIXES = ['.localhost', '.local', '.internal', '.lan',
  '.home.arpa', '.localdomain'];

/** States that mean payloads are readable in transit. */
export const LEAKING = ['plaintext'];

/** The config object of a hook, or an empty object. Pure. */
export function configOf(hook) {
  if (!hook || typeof hook !== 'object') return {};
  const config = hook.config;
  return config && typeof config === 'object' ? config : {};
}

/** The configured URL, trimmed, or ''. Pure. */
export function rawUrl(hook) {
  return String(configOf(hook).url ?? '').trim();
}

/** A URL with its query string and any userinfo removed. Pure. */
export function safeUrl(url) {
  let text = String(url ?? '').trim();
  if (!text) return '';
  text = text.split('?')[0].split('#')[0];
  let scheme = '';
  let rest = text;
  if (text.includes('://')) {
    const idx = text.indexOf('://');
    scheme = text.slice(0, idx);
    rest = text.slice(idx + 3);
  }
  if (rest.includes('@')) {
    rest = `<redacted>@${rest.slice(rest.lastIndexOf('@') + 1)}`;
  }
  return scheme ? `${scheme}://${rest}` : rest;
}

/** The lower-cased scheme of a URL, or ''. Pure. */
export function schemeOf(url) {
  const text = String(url ?? '').trim();
  return text.includes('://') ? text.slice(0, text.indexOf('://')).toLowerCase() : '';
}

/** The lower-cased host of a URL, without port or userinfo. Pure. */
export function hostOf(url) {
  const text = String(url ?? '').trim();
  let rest = text.includes('://') ? text.slice(text.indexOf('://') + 3) : text;
  rest = rest.split('/')[0];
  if (rest.includes('@')) rest = rest.slice(rest.lastIndexOf('@') + 1);
  if (rest.startsWith('[')) return rest.slice(1).split(']')[0].toLowerCase();
  return rest.split(':')[0].toLowerCase();
}

/**
 * Whether a host is somewhere GitHub's delivery network cannot reach. Pure.
 * A name and address test rather than a DNS lookup: a resolver inside your
 * network answers differently from GitHub's.
 */
export function isPrivateHost(host) {
  const name = String(host ?? '').trim().toLowerCase().replace(/^\.+|\.+$/g, '');
  if (!name) return false;
  if (LOCAL_NAMES.includes(name)) return true;
  if (LOCAL_SUFFIXES.some((s) => name.endsWith(s))) return true;
  if (name === '::1' || name === '0:0:0:0:0:0:0:1') return true;
  if (name.includes(':') && (name.startsWith('fd') || name.startsWith('fc') || name.startsWith('fe80:'))) {
    return true;
  }
  const parts = name.split('.');
  if (parts.length === 4 && parts.every((p) => /^[0-9]{1,3}$/.test(p))) {
    const o = parts.map(Number);
    if (o.some((n) => n > 255)) return false;
    if ([0, 127, 10].includes(o[0])) return true;
    if (o[0] === 192 && o[1] === 168) return true;
    if (o[0] === 172 && o[1] >= 16 && o[1] <= 31) return true;
    if (o[0] === 169 && o[1] === 254) return true;
  }
  return false;
}

/** The insecure_ssl value as text, or '' when absent. Pure. */
export function insecureSslReads(hook) {
  const config = configOf(hook);
  if (!('insecure_ssl' in config)) return '';
  return String(config.insecure_ssl).trim().toLowerCase();
}

/** Whether a plaintext hook reads as safe on the field audits sample. Pure. */
export function looksCompliant(hook) {
  const scheme = schemeOf(rawUrl(hook));
  return scheme !== 'https' && scheme !== '' && ['0', 'false'].includes(insecureSslReads(hook));
}

/** Whether the hook has a secret set. Pure. The value is never read. */
export function hasSecret(hook) {
  return 'secret' in configOf(hook);
}

/** Sort one hook into a state and a sentence. Pure. */
export function classify(hook) {
  const ident = `hook ${(hook && typeof hook === 'object' ? hook.id : null) ?? '?'}`;
  const url = rawUrl(hook);
  const scheme = schemeOf(url);
  if (!url || !scheme) {
    return ['no-scheme',
      `${ident} has no usable URL in its config, so nothing can be said about ` +
      'how it delivers.'];
  }
  if (scheme === 'https') {
    if (['1', 'true'].includes(insecureSslReads(hook))) {
      return ['encrypted-unverified',
        `${ident} posts to ${safeUrl(url)} over TLS, but with certificate ` +
        'verification disabled. The transport is encrypted and ' +
        'unauthenticated, which is a different question from this one.'];
    }
    return ['encrypted', `${ident} posts to ${safeUrl(url)} over TLS.`];
  }
  if (scheme !== 'http') {
    return ['unknown-scheme',
      `${ident} posts to a ${scheme}:// URL, which is not a scheme GitHub ` +
      'delivers to. Read the URL by hand.'];
  }
  if (isPrivateHost(hostOf(url))) {
    return ['plaintext-unreachable',
      `${ident} posts to ${safeUrl(url)}, which GitHub cannot route to. This ` +
      'hook has never delivered anything, and it is not leaking payloads either.'];
  }
  const suffix = looksCompliant(hook)
    ? ` insecure_ssl reads "${insecureSslReads(hook)}", which is what a hook ` +
      'with no TLS at all always reads.'
    : '';
  return ['plaintext',
    `${ident} posts to ${safeUrl(url)} over an unencrypted connection.${suffix}`];
}

/** The change to make, printed as a whole config. Pure. */
export function repair(state, hook) {
  if (state === 'plaintext') {
    const rotate = hasSecret(hook)
      ? ' and a new secret. Rotate: that secret has been signing payloads on an open channel.'
      : ' and a secret, since this hook has none.';
    return 'move the receiver behind HTTPS, then send the hook\'s full config ' +
      `with the new URL, the content type${rotate} The config is replaced, ` +
      'not merged, and the secret you read back is a mask.';
  }
  if (state === 'plaintext-unreachable') {
    return 'delete this hook, or point it at an endpoint GitHub can reach ' +
      'over HTTPS. Its delivery log will be connection errors and timeouts ' +
      'for as far back as the retention window goes.';
  }
  if (state === 'encrypted-unverified') {
    return 'this is the certificate-verification question rather than the ' +
      'transport one. Fix the certificate, then set insecure_ssl back to "0" ' +
      'as part of a full config update.';
  }
  if (state === 'no-scheme' || state === 'unknown-scheme') {
    return "read the hook's URL by hand. A hook GitHub cannot parse a scheme " +
      'from is not delivering anything.';
  }
  return 'nothing. This hook delivers over TLS.';
}

/** Counts across every hook read. Pure. */
export function summarize(hooks) {
  const rows = (hooks ?? []).filter((h) => h && typeof h === 'object');
  const states = rows.map((h) => classify(h)[0]);
  const count = (name) => states.filter((s) => s === name).length;
  return {
    total: rows.length,
    plaintext: count('plaintext'),
    unreachable: count('plaintext-unreachable'),
    encrypted: count('encrypted') + count('encrypted-unverified'),
    unreadable: count('no-scheme') + count('unknown-scheme'),
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

  const findings = [];
  for (const scope of scopes) {
    const label = scope.startsWith('@') ? scope.slice(1) : scope;
    const hooks = await listHooks(token, scope);
    const stats = summarize(hooks);
    console.log(`${stats.total} hook(s) on ${label}`);
    for (const hook of hooks) {
      const [state, detail] = classify(hook);
      findings.push({
        scope: label,
        hook_id: hook.id,
        state,
        detail,
        url: safeUrl(rawUrl(hook)),
        looks_compliant: looksCompliant(hook),
      });
      if (state !== 'encrypted') {
        console.log(`${state}: ${detail}`);
        console.log(`repair: ${repair(state, hook)}`);
      }
    }
    if (stats.plaintext === 0) {
      console.log(`encrypted: no hook on ${label} delivers over plaintext ` +
        'HTTP to a routable host');
    }
  }

  console.log(JSON.stringify({ scopes, findings }, null, 2));
  process.exitCode = findings.some((f) => LEAKING.includes(f.state)) ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main() and set an exit code that fails the suite even as
// every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
