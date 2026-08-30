/**
 * Report which webhook signature header your receiver actually verifies.
 *
 * Read only in both senses: every API call is a GET, and the local source scan
 * opens files for reading and prints line numbers rather than lines.
 *
 * GitHub sends X-Hub-Signature (HMAC-SHA1, legacy) and X-Hub-Signature-256
 * (HMAC-SHA256) on every delivery from a hook that has a secret. Which one the
 * receiver checks lives in your source and no API read can see it, so the API
 * half of this script only establishes that both were sent.
 *
 * The secret is never printed; its presence is reported and nothing else.
 *
 * Environment:
 *   GITHUB_TOKEN   a read-only token that can see the repository's hooks
 *
 * Usage:
 *   node github-hook-signature-headers.mjs acme-corp/api ../receiver/src
 */
import { readFile, readdir, stat } from 'node:fs/promises';
import path from 'node:path';

const API = 'https://api.github.com';
const UA = 'github-hook-signature-headers/1.0';

export const MODERN = 'x-hub-signature-256';
export const LEGACY = 'x-hub-signature';

const SUFFIXES = ['.py', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.rb', '.go',
  '.php', '.java', '.kt', '.cs', '.rs', '.ex', '.exs'];
const SKIP_DIRS = ['.git', 'node_modules', 'venv', '.venv', '__pycache__',
  'dist', 'build'];

/** States that mean the reader has something to change. */
export const FINDINGS = ['sha1-only', 'both-accepted', 'no-verification-found'];

/** Lower-cased, with underscores folded to hyphens. Pure. */
export function normalized(text) {
  return String(text ?? '').toLowerCase().replaceAll('_', '-');
}

/** Whether the hook has a secret set: set, absent or unknown. Pure. */
export function secretState(hook) {
  if (!hook || typeof hook !== 'object') return 'unknown';
  const config = hook.config;
  if (!config || typeof config !== 'object') return 'unknown';
  return 'secret' in config ? 'set' : 'absent';
}

/** A copy of a hook config safe to print. Pure. */
export function redactedConfig(config) {
  if (!config || typeof config !== 'object') return {};
  const out = { ...config };
  if ('secret' in out) out.secret = '<set>';
  return out;
}

/** Normalised header names from a delivery record. Pure. Values discarded. */
export function headerNames(headers) {
  let names = [];
  if (Array.isArray(headers)) {
    for (const row of headers) {
      if (row && typeof row === 'object' && row.name) names.push(row.name);
      else if (typeof row === 'string') names.push(row.includes(':') ? row.split(':')[0] : row);
    }
  } else if (headers && typeof headers === 'object') {
    names = Object.keys(headers);
  }
  return names.map((n) => normalized(n).trim());
}

/** Which signature headers GitHub sent on a delivery. Pure. */
export function signatureHeaders(headers) {
  const names = headerNames(headers);
  return { sha256: names.includes(MODERN), sha1: names.includes(LEGACY) };
}

/**
 * Which signature header names a single line refers to. Pure.
 * The modern name is removed first, because the legacy name is a prefix of it.
 */
export function scanLine(line) {
  let norm = normalized(line);
  const kinds = [];
  if (norm.includes(MODERN)) {
    kinds.push('sha256');
    norm = norm.replaceAll(MODERN, ' ');
  }
  if (norm.includes(LEGACY)) kinds.push('sha1');
  return kinds;
}

/** Every signature header reference in a file, as [path, line, kind]. Pure. */
export function scanSource(text, filePath = '<source>') {
  const hits = [];
  const lines = String(text ?? '').split('\n');
  for (let i = 0; i < lines.length; i += 1) {
    for (const kind of scanLine(lines[i])) hits.push([filePath, i + 1, kind]);
  }
  return hits;
}

/** What the scan says the receiver names. Pure. */
export function receiverState(hits) {
  const kinds = new Set((hits ?? []).map(([, , kind]) => kind));
  if (kinds.size === 0) return 'none';
  if (kinds.size === 1 && kinds.has('sha256')) return 'sha256-only';
  if (kinds.size === 1 && kinds.has('sha1')) return 'sha1-only';
  return 'both';
}

/** One line of the scan report. Pure. Never includes source text. */
export function formatHit(hit) {
  const [filePath, number, kind] = hit;
  const name = kind === 'sha256' ? 'X-Hub-Signature-256' : 'X-Hub-Signature';
  const label = kind === 'sha256' ? 'modern' : 'legacy';
  return `${filePath}:${number} ${label} ${name}`;
}

/** Combine the API evidence and the source scan into a finding. Pure. */
export function verdict(secret, sig = null, receiver = null) {
  if (secret === 'absent') {
    return ['no-secret',
      'this hook has no secret, so GitHub sends neither signature header and ' +
      'there is nothing for the receiver to verify. That is a different and ' +
      'larger problem than which digest you use.'];
  }
  if (sig !== null && !sig.sha256 && !sig.sha1) {
    return ['headers-missing',
      'the delivery that was read carries no signature header at all. Either ' +
      'it predates the secret being set, or the record is not a delivery from ' +
      'this hook.'];
  }
  if (receiver === null) {
    return ['not-scanned',
      'GitHub sent the SHA-256 header. Which header the receiver verifies is ' +
      'not visible from the API, so point the scan at the receiver\'s source ' +
      'to get an answer rather than a recommendation.'];
  }
  if (receiver === 'none') {
    return ['no-verification-found',
      'neither signature header name appears in the source that was scanned. ' +
      'Either the receiver does not verify, or it builds the header name at ' +
      'runtime, or the verification lives somewhere the scan was not pointed at.'];
  }
  if (receiver === 'sha1-only') {
    return ['sha1-only',
      'the receiver names only the legacy SHA-1 header. GitHub sent the ' +
      'SHA-256 header on the same request and it is being ignored.'];
  }
  if (receiver === 'both') {
    return ['both-accepted',
      'the receiver names both headers. A receiver that accepts either is ' +
      'exactly as strong as the weaker one, so this is a migration state ' +
      'rather than a finished one.'];
  }
  return ['sha256-only',
    'the receiver names only X-Hub-Signature-256, which is the header to verify.'];
}

/** The change to make, in the reader's own code. Pure. */
export function repair(state) {
  if (state === 'no-secret') {
    return 'set a secret on the hook first. Until there is one, GitHub sends ' +
      'no signature and no digest choice matters.';
  }
  if (state === 'headers-missing') {
    return 'read a delivery from after the secret was set, then re-run.';
  }
  if (state === 'not-scanned') {
    return 're-run with a path to the source tree that handles the webhook.';
  }
  if (state === 'no-verification-found') {
    return 'confirm by hand that the receiver verifies at all. If it does ' +
      'not, verify X-Hub-Signature-256 over the raw request bytes with a ' +
      'constant-time comparison and reject a request whose header is missing.';
  }
  if (state === 'sha1-only' || state === 'both-accepted') {
    return 'verify X-Hub-Signature-256 over the raw request bytes with a ' +
      'constant-time comparison, then delete the SHA-1 branch rather than ' +
      'keeping it as a fallback.';
  }
  return 'nothing. This receiver verifies the header GitHub wants it to.';
}

async function walk(root) {
  const info = await stat(root).catch(() => null);
  if (!info) return [];
  if (info.isFile()) return [root];
  const out = [];
  const entries = await readdir(root, { withFileTypes: true }).catch(() => []);
  for (const entry of entries) {
    if (entry.isDirectory()) {
      if (SKIP_DIRS.includes(entry.name)) continue;
      out.push(...await walk(path.join(root, entry.name)));
    } else if (SUFFIXES.some((s) => entry.name.endsWith(s))) {
      out.push(path.join(root, entry.name));
    }
  }
  return out;
}

async function scanPaths(paths) {
  const hits = [];
  const scanned = [];
  for (const root of paths ?? []) {
    for (const file of await walk(root)) {
      const text = await readFile(file, 'utf8').catch(() => null);
      if (text === null) continue;
      scanned.push(file);
      hits.push(...scanSource(text, file));
    }
  }
  return { hits, scanned };
}

async function get(token, endpoint) {
  const res = await fetch(endpoint.startsWith('/') ? API + endpoint : endpoint, {
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

async function latestDeliveryHeaders(token, repo, hookId) {
  const list = await get(token, `/repos/${repo}/hooks/${hookId}/deliveries?per_page=10`);
  if (list.status !== 200 || !Array.isArray(list.body)) return null;
  for (const row of list.body) {
    if (!row || typeof row !== 'object' || !row.id) continue;
    const one = await get(token, `/repos/${repo}/hooks/${hookId}/deliveries/${row.id}`);
    if (one.status === 200 && one.body && typeof one.body === 'object') {
      const request = one.body.request;
      if (request && typeof request === 'object') return request.headers;
    }
  }
  return null;
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN to a read-only token that can see the ' +
      "repository's hooks");
    process.exitCode = 2;
    return;
  }
  const [repo, ...receivers] = process.argv.slice(2);
  if (!repo) {
    console.error('usage: node github-hook-signature-headers.mjs owner/name [receiver-path...]');
    process.exitCode = 2;
    return;
  }

  const { status, body: hooks } = await get(token, `/repos/${repo}/hooks?per_page=100`);
  if (status !== 200 || !Array.isArray(hooks)) {
    console.error(`GET /repos/${repo}/hooks returned ${status}`);
    process.exitCode = 2;
    return;
  }

  const { hits, scanned } = await scanPaths(receivers);
  const stateOfReceiver = receivers.length ? receiverState(hits) : null;
  if (receivers.length) {
    const files = new Set(hits.map(([f]) => f));
    console.log(`source scan: ${hits.length} reference(s) across ${files.size} file(s)`);
    for (const hit of hits) console.log(`  ${formatHit(hit)}`);
  }

  const findings = [];
  for (const hook of hooks) {
    const secret = secretState(hook);
    const headers = secret === 'set'
      ? await latestDeliveryHeaders(token, repo, hook.id)
      : null;
    const sig = headers !== null && headers !== undefined ? signatureHeaders(headers) : null;
    const [state, detail] = verdict(secret, sig, stateOfReceiver);
    console.log(`hook ${hook.id}: secret is ${secret}, ` +
      (sig && sig.sha1 && sig.sha256
        ? 'GitHub sent both signature headers'
        : 'no delivery headers were read'));
    console.log(`${state}: ${detail}`);
    console.log(`repair: ${repair(state)}`);
    findings.push({
      hook_id: hook.id,
      secret,
      signature_headers: sig,
      state,
      detail,
      config: redactedConfig(hook.config),
    });
  }

  console.log(JSON.stringify({
    repo,
    files_scanned: scanned.length,
    references: hits.map(formatHit),
    findings,
  }, null, 2));
  process.exitCode = findings.some((f) => FINDINGS.includes(f.state)) ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main() and set an exit code that fails the suite even as
// every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
