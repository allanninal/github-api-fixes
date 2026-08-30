/**
 * Decide which authentication mechanism a GitHub client is using, offline.
 *
 * Read only, and one request: GET /user with a Bearer header built from the
 * environment. The script deliberately never transmits a username and
 * password, even to reproduce the documented 401.
 *
 * Nothing here prints the secret. The report carries the scheme, the length
 * and whether a username was present.
 */
import { readFile } from 'node:fs/promises';

const API = 'https://api.github.com';
const UA = 'github-auth-scheme-check/1.0';

/** Prefixes GitHub issues its tokens with. */
export const TOKEN_PREFIXES = ['ghp_', 'gho_', 'ghu_', 'ghs_', 'ghr_', 'github_pat_'];
const LEGACY_HEX = /^[0-9a-f]{40}$/;

/** The message that means the mechanism is retired rather than the credential wrong. */
export const REMOVED = 'support for password authentication was removed';

/** Call sites that build the retired header without the word Authorization. */
export const CALL_SITES = [
  ['curl -u', /\bcurl\b[^\n]*?\s(-u|--user)\s/],
  ['Invoke-WebRequest -Credential', /-Credential\b/],
  ['netrc entry', /^\s*machine\s+[\w.]*github/i],
  ['two-string client constructor', /\b(username|user)\s*=\s*[^,\n]+,\s*password\s*=/],
];

/** Is this string shaped like a GitHub token rather than a password? Pure. */
export function looksLikeToken(secret) {
  const value = String(secret ?? '');
  if (TOKEN_PREFIXES.some((p) => value.startsWith(p))) return true;
  return LEGACY_HEX.test(value);
}

/**
 * Describe an Authorization header without revealing what is in it. Pure.
 * The secret is never part of the return value, so no caller can log it.
 */
export function parseAuthHeader(value) {
  const raw = String(value ?? '').trim();
  if (!raw) {
    return { scheme: null, username_present: false, secret_length: 0,
      token_shaped: false, decoded: true };
  }
  const space = raw.indexOf(' ');
  const scheme = (space === -1 ? raw : raw.slice(0, space)).toLowerCase();
  const rest = space === -1 ? '' : raw.slice(space + 1).trim();

  if (scheme !== 'basic') {
    return { scheme, username_present: false, secret_length: rest.length,
      token_shaped: looksLikeToken(rest), decoded: true };
  }

  let decoded;
  try {
    const buf = Buffer.from(rest, 'base64');
    if (buf.toString('base64').replace(/=+$/, '') !== rest.replace(/=+$/, '')) {
      throw new Error('not base64');
    }
    decoded = buf.toString('utf8');
  } catch {
    return { scheme: 'basic', username_present: false, secret_length: 0,
      token_shaped: false, decoded: false };
  }

  const colon = decoded.indexOf(':');
  const user = colon === -1 ? decoded : decoded.slice(0, colon);
  const secret = colon === -1 ? '' : decoded.slice(colon + 1);
  return { scheme: 'basic', username_present: Boolean(user) || colon !== -1,
    secret_length: secret.length, token_shaped: looksLikeToken(secret),
    decoded: true };
}

/** Name the mechanism from a parsed header. Pure. */
export function classify(parsed) {
  const scheme = (parsed ?? {}).scheme;
  if (scheme === null || scheme === undefined) return 'no-credential';
  if (scheme === 'basic') {
    if (!parsed.decoded) return 'undecodable-basic';
    return parsed.token_shaped ? 'token-basic' : 'password-basic';
  }
  if (scheme === 'bearer') return 'bearer';
  if (scheme === 'token') return 'token-scheme';
  return 'unknown-scheme';
}

/** Does this response body carry the retired-mechanism message? Pure. */
export function passwordRemoved(body) {
  const text = (body && typeof body === 'object')
    ? String(body.message ?? '') : String(body ?? '');
  return text.toLowerCase().split(/\s+/).join(' ').includes(REMOVED);
}

/** The one line that replaces every retired form. Pure. */
export function replacementHeader() {
  return 'Authorization: Bearer $GITHUB_TOKEN';
}

/**
 * Find call sites that build a username-and-password header. Pure.
 * Reports the line number and the shape, never the line itself.
 */
export function scanSnippet(text) {
  const findings = [];
  const lines = String(text ?? '').split('\n');
  for (let i = 0; i < lines.length; i += 1) {
    for (const [label, pattern] of CALL_SITES) {
      if (pattern.test(lines[i])) findings.push({ line: i + 1, form: label });
    }
  }
  return findings;
}

/** Turn the classification and the Bearer probe into a finding. Pure. */
export function verdict(kind, probeStatus, probeBody) {
  if (kind === 'password-basic') {
    return ['password-basic',
      'the header is a username and a password. That mechanism was removed ' +
      'from the API and no password will ever be accepted again. Nothing was ' +
      'sent: the shape is the answer, and transmitting it would only add a ' +
      'copy of the password to your proxy log.'];
  }
  if (kind === 'token-basic') {
    return ['token-basic',
      'the header is a username and a token. That still works on much of the ' +
      'API, which is why it survives, but the username is meaningless and the ' +
      'form is on the way out. Replace it.'];
  }
  if (kind === 'undecodable-basic') {
    return ['undecodable-basic',
      'the header says Basic but the payload is not valid base64, so ' +
      'something is double-encoding or truncating it before it goes out. ' +
      'GitHub will read this as no credential at all.'];
  }
  if (kind === 'no-credential') {
    return ['no-credential',
      'no Authorization header was configured, so requests go out anonymous ' +
      'rather than refused, and quietly get the 60 an hour tier instead of an error.'];
  }
  if (kind === 'unknown-scheme') {
    return ['unknown-scheme',
      'the scheme is neither Basic, Bearer nor token, so GitHub will ignore ' +
      'it and treat the request as unauthenticated.'];
  }

  if (probeStatus === 200) {
    return ['ok', 'the documented scheme, and the credential behind it authenticates.'];
  }
  if (passwordRemoved(probeBody)) {
    return ['password-removed-message',
      'the scheme looks right but GitHub still answered with the ' +
      'retired-mechanism message, so something downstream is rewriting the ' +
      'header into Basic before it leaves.'];
  }
  if (probeStatus === 401) {
    return ['credential-rejected',
      'the mechanism is correct and the credential is not. That is a ' +
      'different problem from this one: the token is wrong, revoked or ' +
      'expired rather than badly wrapped.'];
  }
  return ['probe-inconclusive',
    `the scheme is correct; the probe returned ${probeStatus} rather than 200, ` +
    'so judge the credential separately.'];
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

async function main() {
  const configured = (process.env.GITHUB_AUTH_HEADER || "dummy-github-auth-header");
  const parsed = parseAuthHeader(configured);
  const kind = classify(parsed);
  console.log(`scheme: ${kind}, secret ${parsed.secret_length} char(s), ` +
    `${parsed.username_present ? 'username present' : 'no username'}`);

  if (kind === 'password-basic') {
    console.warn('not sending this header. A password is refused by every ' +
      'endpoint, and posting it would put a live password in one more log');
  }

  let probeStatus = null;
  let probeBody = null;
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (token) {
    const probe = await get(token, '/user');
    probeStatus = probe.status;
    probeBody = probe.body;
    const who = probeBody?.login ?? 'an unnamed user';
    console.log(`probe: GET /user returned ${probeStatus}` +
      (probeStatus === 200 ? ` as ${who}` : ''));
  } else {
    console.log('set GITHUB_TOKEN to also prove the credential itself is good');
  }

  const [state, detail] = verdict(kind, probeStatus, probeBody);
  console.log(`${state}: ${detail}`);

  if (['password-basic', 'token-basic', 'undecodable-basic', 'no-credential',
    'unknown-scheme'].includes(state)) {
    console.log(`repair: send exactly this and delete the username field: ${replacementHeader()}`);
    console.log('repair: Authorization: token TOKEN is still accepted if a ' +
      'library will not emit Bearer, but Basic is not worth keeping.');
  }

  let sites = [];
  const snippetFile = process.argv[2];
  if (snippetFile) {
    sites = scanSnippet(await readFile(snippetFile, 'utf8'));
    for (const site of sites) {
      console.warn(`line ${site.line} builds the retired header via ${site.form}`);
    }
    if (!sites.length) {
      console.log(`no call sites in ${snippetFile} build a username and password header`);
    }
  }

  console.log(JSON.stringify({
    scheme: kind, username_present: parsed.username_present,
    secret_length: parsed.secret_length, probe_status: probeStatus,
    call_sites: sites, state,
  }, null, 2));
  process.exitCode = state === 'ok' ? 0 : 1;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main() and set an exit code that fails the suite.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
