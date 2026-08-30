/**
 * Check the Authorization scheme word against the shape of the credential.
 *
 * Read only. The diagnosis is local: a credential announces its own type in
 * its first few characters, so the pairing is decided before a socket opens.
 * The only network work is two GETs to the same path differing in one word.
 *
 * The credential value is never printed, logged or returned. Only its type is.
 */
const API = 'https://api.github.com';
const UA = 'github-auth-scheme/1.0 (+https://example.com/contact)';

/** GitHub's prefixed credential formats. */
export const PREFIXES = [
  ['github_pat_', 'fine-grained-pat'],
  ['ghp_', 'classic-pat'],
  ['gho_', 'oauth-user-token'],
  ['ghu_', 'user-to-server-token'],
  ['ghs_', 'installation-token'],
  ['ghr_', 'refresh-token'],
];

/** What each credential type accepts in front of it, lowercased. */
export const ACCEPTS = {
  'app-jwt': ['bearer'],
  'classic-pat': ['bearer', 'token'],
  'fine-grained-pat': ['bearer', 'token'],
  'oauth-user-token': ['bearer', 'token'],
  'user-to-server-token': ['bearer', 'token'],
  'installation-token': ['bearer', 'token'],
  'legacy-pat': ['bearer', 'token'],
  'refresh-token': [],
  unknown: ['bearer', 'token'],
  absent: [],
};

const B64URL = /^[A-Za-z0-9_=-]+$/;

/**
 * Recognise a JWT by shape alone. Pure.
 * The payload is deliberately not decoded: what the claims say is a different
 * question and a different note.
 */
export function looksLikeJwt(value) {
  if (!value) return false;
  const parts = String(value).split('.');
  if (parts.length !== 3 || parts.some((p) => !p)) return false;
  if (!parts.every((p) => B64URL.test(p))) return false;
  return parts[0].startsWith('eyJ');
}

/** Name a credential's type from its own text. Pure. Never returns the value. */
export function credentialKind(value) {
  if (value === null || value === undefined || !String(value).trim()) return 'absent';
  const text = String(value).trim();
  if (looksLikeJwt(text)) return 'app-jwt';
  for (const [prefix, kind] of PREFIXES) {
    if (text.startsWith(prefix)) return kind;
  }
  if (text.length === 40 && /^[0-9a-f]+$/.test(text.toLowerCase())) return 'legacy-pat';
  return 'unknown';
}

/** Split an Authorization header into a scheme and whether a value follows. Pure. */
export function parseAuthorization(header) {
  if (header === null || header === undefined) {
    return { scheme: null, hasCredential: false, words: 0 };
  }
  const words = String(header).split(/\s+/).filter(Boolean);
  if (!words.length) return { scheme: null, hasCredential: false, words: 0 };
  if (words.length === 1) return { scheme: null, hasCredential: true, words: 1 };
  return { scheme: words[0], hasCredential: true, words: words.length };
}

/** Decide whether a scheme word and a credential type belong together. Pure. */
export function checkPairing(scheme, kind) {
  const word = String(scheme ?? '').toLowerCase();
  if (kind === 'absent') {
    return ['no-credential',
      'there is no credential to pair a scheme with. The variable holding it ' +
      'is empty or unset.',
      'set the credential in the environment and read it from there'];
  }
  if (word === 'basic') {
    return ['basic-scheme',
      'Basic is a retired mechanism for this API. It fails for a reason that ' +
      'has nothing to do with which credential you hold.',
      'send Authorization: Bearer with the token instead of Basic'];
  }
  if (scheme === null || scheme === undefined) {
    return ['scheme-missing',
      'the header carries a bare value with no word in front of it. GitHub ' +
      'cannot tell what it is being offered and refuses it with the same ' +
      'message a dead token gets.',
      'prefix the value with Bearer and a single space'];
  }
  if (word !== 'bearer' && word !== 'token') {
    return ['unknown-scheme',
      `${scheme} is not a scheme this API reads. Only Bearer and the legacy ` +
      'token word are accepted.',
      'replace the scheme word with Bearer'];
  }
  if (kind === 'refresh-token') {
    return ['refresh-token-sent',
      'a refresh token is not an API credential under any scheme. It is ' +
      'exchanged for a user token, and that result is what goes on the wire.',
      'exchange the refresh token first, then send what comes back'];
  }
  if (kind === 'app-jwt' && word === 'token') {
    return ['jwt-with-token-scheme',
      'an App JWT is only read under Bearer. Under the token word it is ' +
      'refused with the generic bad credentials message, which names the ' +
      'credential and hides the envelope.',
      'change the word token to Bearer and send the same JWT'];
  }
  if (word === 'token') {
    return ['legacy-scheme-accepted',
      'the token word still works for this credential type, so nothing is ' +
      'failing because of it today. It is the older spelling, and it is the ' +
      'one that breaks when the same code path later carries a JWT.',
      'move this helper to Bearer for every credential type'];
  }
  return ['bearer-ok',
    'Bearer is correct for this credential type, so the envelope is not the ' +
    'problem. If the call still fails, the credential itself is the subject.',
    'none'];
}

/** The short set of sentences GitHub uses for an authentication failure. */
export const MESSAGES = [
  ['a json web token could not be decoded',
    ['jwt-expected',
      'the endpoint wanted an App JWT and got something that is not one. That ' +
      'is a credential type mismatch rather than a scheme one, and it is the ' +
      'helpful failure: it names its subject.']],
  ['requires authentication',
    ['nothing-arrived',
      'no Authorization header reached GitHub at all, so the scheme word is ' +
      'not the question yet. Something between your process and GitHub ' +
      'dropped the header.']],
  ['bad credentials',
    ['received-and-refused',
      'GitHub parsed something and did not accept it. A JWT under the token ' +
      'word produces exactly this, and so does a dead token, so the message ' +
      'alone does not separate them.']],
];

/** Map GitHub's authentication messages onto causes. Pure. */
export function explain401(message) {
  const text = String(message ?? '').trim().toLowerCase().replace(/\.+$/, '');
  for (const [needle, result] of MESSAGES) {
    if (text.includes(needle)) return result;
  }
  return ['unmapped-message',
    'not one of the sentences GitHub uses for an authentication failure, so ' +
    'read it literally rather than through this table.'];
}

async function get(path, scheme, token) {
  const url = path.startsWith('/') ? API + path : path;
  const res = await fetch(url, {
    headers: {
      Authorization: `${scheme} ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': UA,
    },
  });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return [res.status, body && typeof body === 'object' ? body.message ?? null : null];
}

function arg(name, fallback) {
  const i = process.argv.indexOf(name);
  return i === -1 ? fallback : process.argv[i + 1];
}

async function main() {
  const path = arg('--path', '/user');
  const scheme = arg('--scheme', 'token');
  const offline = process.argv.includes('--offline');

  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const kind = credentialKind(token);
  if (kind === 'absent') {
    console.error('set GITHUB_TOKEN. There is no credential to pair a scheme ' +
      'with, which is its own answer but not this one');
    process.exitCode = 2;
    return;
  }

  const header = parseAuthorization(`${scheme} ${token}`);
  const [state, detail, repair] = checkPairing(header.scheme, kind);

  console.log(`credential type: ${kind}`);
  console.log(`scheme word:     ${header.scheme ?? 'none, a bare value'}`);
  console.log(`accepted words:  ${(ACCEPTS[kind] ?? []).join(', ') || 'none'}`);
  console.log(`${state}: ${detail}`);

  const result = { path, credentialType: kind, scheme: header.scheme, state };

  if (!offline) {
    const [status, message] = await get(path, scheme, token);
    console.log(`as configured (${scheme}):  ${status} ${message ?? ''}`);
    if (status === 401) {
      const [cause, why] = explain401(message);
      console.log(`  ${cause}: ${why}`);
    }
    result.configured = { scheme, status, message };

    if (scheme.toLowerCase() !== 'bearer') {
      const [status2, message2] = await get(path, 'Bearer', token);
      console.log(`as recommended (Bearer): ${status2} ${message2 ?? ''}`);
      result.recommended = { scheme: 'Bearer', status: status2, message: message2 };
      if (status2 !== status) {
        console.log('the scheme word alone changed the outcome, which is as ' +
          'close to proof as this gets');
      } else if (status >= 400) {
        console.log('both words failed identically, so the envelope is ' +
          'innocent. Look at the credential itself, the account it belongs ' +
          "to, or the endpoint's own rules");
      }
    }
  }

  if (repair !== 'none') console.log(`repair: ${repair}`);
  console.log(JSON.stringify(result, null, 2));
  process.exitCode = ['jwt-with-token-scheme', 'scheme-missing', 'unknown-scheme',
    'basic-scheme', 'refresh-token-sent'].includes(state) ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not fire live requests and set an exit code the suite then inherits.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
