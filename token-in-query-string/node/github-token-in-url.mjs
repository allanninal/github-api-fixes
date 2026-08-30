/**
 * Find GitHub credentials sitting in URLs, without ever printing one.
 *
 * Read only, and one request: GET /rate_limit with the credential in an
 * Authorization header. That call spends no quota and answers the only
 * question the API can answer here.
 *
 * This never issues a request with a credential in the query string, not even
 * to reproduce the documented anonymous-tier reading, and it never emits a
 * credential value. Findings carry a shape, a length and a truncated digest.
 */
import { createHash, timingSafeEqual } from 'node:crypto';
import { readFile } from 'node:fs/promises';

const API = 'https://api.github.com';
const UA = 'github-token-in-url/1.0';

/** Documented token prefixes. The shape is not itself a secret. */
export const PREFIXES = [
  ['github_pat_', 'fine-grained-pat'],
  ['ghp_', 'classic-pat'],
  ['gho_', 'oauth-token'],
  ['ghu_', 'app-user-token'],
  ['ghs_', 'app-installation-token'],
  ['ghr_', 'refresh-token'],
];
const LEGACY_HEX = /^[0-9a-f]{40}$/;

/** Parameter names that carry a credential whatever the value looks like. */
export const SUSPECT_NAMES = new Set(['access_token', 'token', 'oauth_token',
  'api_key', 'apikey', 'client_secret', 'private_token', 'auth', 'password',
  'secret']);

/**
 * Parameter names whose values are legitimately forty hex characters. Without
 * these, every commit SHA in a URL is reported as a legacy token.
 */
export const GIT_OBJECT_NAMES = new Set(['sha', 'commit_sha', 'head_sha',
  'base_sha', 'tree_sha', 'oid', 'ref', 'base', 'head']);

const URL_PATTERN = /https?:\/\/[^\s<>]+/g;

/** Punctuation a log line puts after a URL. */
const TRAILING = /["'>),.;]+$/;

export const REDACTED = 'REDACTED';

/** Name the kind of credential a string looks like. Pure. */
export function shapeOf(value) {
  const text = String(value ?? '');
  for (const [prefix, name] of PREFIXES) {
    if (text.startsWith(prefix)) return name;
  }
  if (LEGACY_HEX.test(text)) return 'legacy-hex40';
  return text.length >= 16 ? 'opaque' : 'short';
}

/** A twelve-character digest, for correlating two sightings. Pure. */
export function fingerprint(value) {
  const digest = createHash('sha256').update(String(value ?? ''), 'utf8').digest('hex');
  return `sha256:${digest.slice(0, 12)}`;
}

/** Do two fingerprints describe the same value? Pure. */
export function sameCredential(left, right) {
  if (!left || !right) return false;
  const a = Buffer.from(String(left));
  const b = Buffer.from(String(right));
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

/** Every URL in a blob of log or configuration. Pure. */
export function urlsIn(text) {
  return (String(text ?? '').match(URL_PATTERN) ?? [])
    .map((u) => u.replace(TRAILING, ''));
}

function paramsOf(url) {
  try {
    return new URL(String(url ?? '')).searchParams;
  } catch {
    return null;
  }
}

/**
 * Would this query parameter be reported as carrying a credential? Pure.
 * The single place that decision is made, so the reporter and the redactor
 * cannot drift apart about what counts as a secret.
 */
export function isCredential(name, value) {
  const lowered = String(name ?? '').toLowerCase();
  if (SUSPECT_NAMES.has(lowered)) return true;
  if (GIT_OBJECT_NAMES.has(lowered)) return false;
  const shape = shapeOf(value);
  return shape !== 'short' && shape !== 'opaque';
}

/** Credential-bearing query parameters in one URL. Pure. */
export function credentialParams(url) {
  const params = paramsOf(url);
  if (!params) return [];
  const out = [];
  for (const [name, value] of params.entries()) {
    if (!isCredential(name, value)) continue;
    out.push({
      param: name,
      shape: shapeOf(value),
      length: value.length,
      fingerprint: fingerprint(value),
      ignored_by_github: name.toLowerCase() === 'access_token',
    });
  }
  return out;
}

/** The same URL with every credential-bearing value replaced. Pure. */
export function redact(url) {
  let parsed;
  try {
    parsed = new URL(String(url ?? ''));
  } catch {
    return REDACTED;
  }
  if (!parsed.search) return String(url ?? '');
  const next = new URLSearchParams();
  for (const [name, value] of parsed.searchParams.entries()) {
    next.append(name, isCredential(name, value) ? REDACTED : value);
  }
  parsed.search = next.toString();
  return parsed.toString();
}

/** Findings across many labelled URLs, with the redacted form attached. Pure. */
export function audit(entries) {
  const findings = [];
  for (const [label, url] of entries ?? []) {
    for (const hit of credentialParams(url)) {
      findings.push({ ...hit, where: label, redacted: redact(url) });
    }
  }
  return findings;
}

/** Turn the findings into a decision about revocation. Pure. */
export function verdict(findings, live, heldFingerprint) {
  if (!findings || !findings.length) {
    return ['no-credential-in-url',
      'no query parameter carried a credential-shaped value.'];
  }

  const matched = findings.filter((f) => sameCredential(f.fingerprint, heldFingerprint));
  const ignored = findings.filter((f) => f.ignored_by_github);
  const distinct = new Set(findings.map((f) => f.fingerprint)).size;

  const tail = ignored.length
    ? ` ${ignored.length} of them use access_token, which GitHub ignores ` +
      'outright, so those requests went out anonymous rather than authenticated.'
    : '';

  if (matched.length && live) {
    return ['live-credential-in-url',
      `${findings.length} occurrence(s) of ${distinct} distinct credential(s) ` +
      'in URLs, and one of them is the credential this process is holding, ' +
      'which still authenticates. Revoke it; relocating it to a header does ' +
      `not unwrite the log lines.${tail}`];
  }
  if (matched.length) {
    return ['dead-credential-in-url',
      `${findings.length} occurrence(s) in URLs match the credential this ` +
      'process holds, and that credential no longer authenticates. The ' +
      `exposure is historical, but the habit that created it is not.${tail}`];
  }
  return ['credential-in-url',
    `${findings.length} occurrence(s) of ${distinct} distinct ` +
    'credential-shaped value(s) in URLs. None match the credential this ' +
    'process holds, so their liveness cannot be judged from here; treat them ' +
    `as live until somebody proves otherwise.${tail}`];
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
  return { status: res.status };
}

async function main() {
  const args = process.argv.slice(2);
  const fileIndex = args.indexOf('--from-file');
  const fromFile = fileIndex === -1 ? null : args[fileIndex + 1];
  const urlArgs = args.filter((a, i) =>
    a !== '--from-file' && i !== fileIndex + 1);

  const entries = urlArgs.map((u, i) => [`argv[${i + 1}]`, u]);
  if (fromFile) {
    const lines = (await readFile(fromFile, 'utf8')).split('\n');
    for (let i = 0; i < lines.length; i += 1) {
      for (const url of urlsIn(lines[i])) entries.push([`${fromFile}:${i + 1}`, url]);
    }
  }

  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  let heldFingerprint = null;
  let live = false;
  if (token) {
    heldFingerprint = fingerprint(token);
    // In the header, never in the URL. GET /rate_limit spends no quota.
    const rate = await get(token, '/rate_limit');
    live = rate.status === 200;
    console.log(`credential in this process: ${heldFingerprint}, ` +
      (live ? 'still live' : `not accepted (status ${rate.status})`));
  } else {
    console.log('set GITHUB_TOKEN to also learn whether a credential found in ' +
      'a URL is the one you are holding, and whether it still works');
  }

  console.log(`scanned ${entries.length} url(s)`);
  const findings = audit(entries);
  for (const item of findings) {
    console.warn(`${item.where} carries ${item.fingerprint} (${item.shape}, ` +
      `${item.length} chars) in ?${item.param}= ; scrubbed: ${item.redacted}`);
  }

  const [state, detail] = verdict(findings, live, heldFingerprint);
  console.log(`${state}: ${detail}`);

  if (findings.length) {
    console.log('repair: move the credential into Authorization: Bearer TOKEN ' +
      'on every request, including inside any client wrapper that appends ' +
      'parameters for you.');
    console.log('repair: revoke and re-mint before scrubbing. Revocation takes ' +
      'seconds and log retention takes days.');
    console.log('note: this script cannot enumerate where a URL has already ' +
      'been written, and it will not reproduce the leak to measure it.');
  }

  console.log(JSON.stringify({ scanned: entries.length, findings, state }, null, 2));
  process.exitCode = findings.length ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main() and set an exit code that fails the suite.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
