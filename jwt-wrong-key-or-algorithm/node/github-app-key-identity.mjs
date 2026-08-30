/**
 * Say which GitHub App a private key belongs to, without printing the key.
 *
 * Read only. One request, GET /app, sent with a JWT you already hold. The
 * script never signs anything itself: it inspects the key file and asks
 * GitHub who answered.
 *
 * The output contains a PEM label, a line count, a byte count and a truncated
 * SHA-256 fingerprint. It never contains the key or the JWT.
 *
 * The blind spot is stated rather than worked around: GitHub does not publish
 * the public keys registered on an App, so nothing here can prove a key is
 * registered except by using it.
 */
import { createHash } from 'node:crypto';

const API = 'https://api.github.com';
const UA = 'github-app-key-identity/1.0';

/** The two characters backslash and n, which is what an escaped PEM holds. */
export const ESCAPED_NEWLINE = '\\n';

/** A 2048-bit RSA private key is around 1200 bytes of DER. */
export const MIN_RSA_DER = 500;

/** The labels GitHub issues, and the PKCS#8 alternative libraries accept. */
export const USABLE_LABELS = {
  'RSA PRIVATE KEY': 'pkcs1-rsa-key',
  'PRIVATE KEY': 'pkcs8-key',
};

const REPAIRS = {
  'no-key-present':
    'set GITHUB_APP_PRIVATE_KEY to the PEM downloaded from the App\'s ' +
    'settings page. Nothing can be said about a key that is not there.',
  'escaped-newlines':
    'the value contains the two characters backslash and n where line breaks ' +
    'belong, so some layer between the settings page and this process escaped ' +
    'them. Base64-encode the whole PEM for transport and decode it in the ' +
    'process; then no layer in between has an opinion.',
  'single-line-pem':
    'the PEM has lost its line breaks entirely. Same repair: carry it ' +
    'base64-encoded rather than raw.',
  'not-a-pem':
    'there is no BEGIN line, so this is not a PEM at all. Check what the ' +
    'secret store actually returned.',
  'truncated-pem':
    'there is a BEGIN line and no matching END line, so the value was cut ' +
    'short. Secret stores with a length limit do this quietly.',
  'encrypted-key':
    'this key is passphrase-protected. GitHub does not issue encrypted keys, ' +
    'so this one was re-encrypted locally; decrypt it or download a fresh key.',
  'openssh-format':
    'this is an OpenSSH key, which is what ssh-keygen produces. It is not the ' +
    'key GitHub issued for the App.',
  'public-key-not-private':
    'this is the public half of a pair. The public key cannot sign, so no JWT ' +
    'made with it will ever verify.',
  'certificate-not-key':
    'this is a certificate rather than a key. Something is reading the wrong ' +
    'entry out of the secret store.',
  'not-an-rsa-key':
    'GitHub App JWTs must be signed RS256, which needs an RSA key. This key ' +
    'uses a different algorithm family and cannot sign one.',
  'unknown-pem-label':
    'the PEM label is not one this check recognises, which usually means the ' +
    'wrong file entirely.',
  'body-not-base64':
    'the body between the BEGIN and END lines is not valid base64, so the PEM ' +
    'was corrupted in transit or edited by hand.',
  'too-small-for-rsa':
    'the decoded body is too small to be an RSA private key of any usable ' +
    'size, so this is either truncated or a different kind of key.',
  'pkcs1-rsa-key': 'this is the PKCS#1 RSA private key GitHub issues.',
  'pkcs8-key': 'this is a PKCS#8 wrapper, which every sensible JWT library accepts.',
};

/**
 * Undo base64 transport if the value is a wrapped PEM. Pure.
 * Returns [pem, wasWrapped].
 */
export function unwrap(text) {
  const raw = String(text ?? '').trim();
  if (!raw || raw.includes('BEGIN')) return [raw, false];
  try {
    const decoded = Buffer.from(raw, 'base64').toString('utf8');
    if (decoded.includes('BEGIN')) return [decoded, true];
  } catch {
    return [raw, false];
  }
  return [raw, false];
}

const isBase64 = (text) => /^[A-Za-z0-9+/]*={0,2}$/.test(text) && text.length % 4 === 0;

/**
 * Reduce a PEM to a label, a shape and a fingerprint. Pure.
 * Never returns any part of the key.
 */
export function inspectPem(text) {
  const raw = String(text ?? '');
  const out = { state: null, label: null, fingerprint: null, der_bytes: null, lines: 0 };
  if (!raw.trim()) {
    out.state = 'no-key-present';
    return out;
  }
  out.lines = raw.trim().split('\n').length;
  if (raw.includes(ESCAPED_NEWLINE)) {
    out.state = 'escaped-newlines';
    return out;
  }
  const found = /-----BEGIN ([A-Z0-9 ]+)-----/.exec(raw);
  if (!found) {
    out.state = 'not-a-pem';
    return out;
  }
  const label = found[1].trim();
  out.label = label;

  if (label === 'ENCRYPTED PRIVATE KEY' || raw.includes('Proc-Type: 4,ENCRYPTED')) {
    out.state = 'encrypted-key';
    return out;
  }
  if (label === 'OPENSSH PRIVATE KEY') { out.state = 'openssh-format'; return out; }
  if (label.endsWith('PUBLIC KEY')) { out.state = 'public-key-not-private'; return out; }
  if (label === 'CERTIFICATE') { out.state = 'certificate-not-key'; return out; }
  if (label === 'EC PRIVATE KEY' || label === 'DSA PRIVATE KEY') {
    out.state = 'not-an-rsa-key';
    return out;
  }
  if (!(label in USABLE_LABELS)) { out.state = 'unknown-pem-label'; return out; }
  if (!raw.includes(`-----END ${label}-----`)) { out.state = 'truncated-pem'; return out; }
  if (out.lines < 3) { out.state = 'single-line-pem'; return out; }

  const body = raw.split('\n').map((l) => l.trim())
    .filter((l) => l && !l.startsWith('-----')).join('');
  if (!isBase64(body)) { out.state = 'body-not-base64'; return out; }
  const der = Buffer.from(body, 'base64');
  out.der_bytes = der.length;
  out.fingerprint = createHash('sha256').update(der).digest('hex').slice(0, 16);
  if (der.length < MIN_RSA_DER) { out.state = 'too-small-for-rsa'; return out; }
  out.state = USABLE_LABELS[label];
  return out;
}

/** Whether a key in this state could sign an RS256 JWT at all. Pure. */
export function usable(state) {
  return state === 'pkcs1-rsa-key' || state === 'pkcs8-key';
}

/** The one sentence worth printing under a PEM state. Pure. */
export function repairFor(state) {
  return REPAIRS[state] ?? 'this state has no stock repair; read the label.';
}

/**
 * Classify what was put in the iss claim. Pure.
 * It must be the client ID or the numeric App ID; anything else returns
 * Integration not found, which is a different failure from a bad key.
 */
export function issuerForm(value) {
  const text = String(value ?? '').trim();
  if (!text) return 'no-issuer';
  if (/^\d+$/.test(text)) return 'app-id';
  if (text.startsWith('Iv1.') || text.startsWith('Iv23')) return 'client-id';
  return 'unusable-issuer';
}

/** Map a GET /app response to the defect it names. Pure. */
export function interpret(status, message) {
  if (status === 200) {
    return ['key-accepted', 'the JWT verified against a key registered on this App.'];
  }
  const text = String(message ?? '').toLowerCase();
  if (text.includes('could not be decoded')) {
    return ['signature-rejected',
      'GitHub could not verify the JWT. That one message covers a key from ' +
      'another App, a key deleted during rotation, an algorithm other than ' +
      'RS256, and a PEM whose newlines were destroyed. Compare the ' +
      'fingerprint against a machine that works to split the list.'];
  }
  if (text.includes('integration not found')) {
    return ['issuer-does-not-resolve',
      'iss does not name an App GitHub can find, so the claim is wrong rather ' +
      'than the key. It must be the client ID or the numeric App ID.'];
  }
  if (text.includes('issued at') || text.includes("'iat'")) {
    return ['clock-problem-not-key',
      'GitHub is complaining about iat, which is clock drift on the signing ' +
      'host and a different repair entirely.'];
  }
  if (text.includes('too far in the future')) {
    return ['lifetime-problem-not-key',
      'GitHub is complaining about exp, so the requested lifetime is over the ' +
      'ceiling and the key is fine.'];
  }
  if (text.includes('bad credentials')) {
    return ['not-a-jwt',
      'GitHub parsed the credential and refused it outright, which is what ' +
      'happens when an installation access token is sent to a route that ' +
      'wants the App JWT.'];
  }
  return ['unrelated',
    'the response does not name a key or a claim, so this failure has another cause.'];
}

/** Say whether GET /app answered as the App you meant. Pure. */
export function reconcile(app, expected) {
  if (!app || typeof app !== 'object' || Array.isArray(app)) {
    return ['no-app-body', 'GET /app returned nothing that could be read as an App.'];
  }
  const label = `${app.slug ?? app.name} (id ${app.id}, client_id ${app.client_id})`;
  const known = new Set(['id', 'client_id', 'slug', 'name']
    .map((field) => String(app[field] ?? '').toLowerCase())
    .filter(Boolean));
  const want = String(expected ?? '').trim().toLowerCase();
  if (!want) {
    return ['no-expectation-given',
      `GET /app answered as ${label}. Pass --expect to have that checked ` +
      'rather than reported.'];
  }
  if (known.has(want)) return ['identity-matches', `GET /app answered as ${label}.`];
  return ['authenticated-as-another-app',
    `you expected ${expected} and the key authenticated as ${label}. The ` +
    'credential works; it belongs to a different App, which is how a staging ' +
    'key reaches production without anything failing.'];
}

function flag(name) {
  const at = process.argv.indexOf(name);
  return (at === -1 || at === process.argv.length - 1) ? null : process.argv[at + 1];
}

async function main() {
  const [pem, wrapped] = unwrap((process.env.GITHUB_APP_PRIVATE_KEY || "dummy-github-app-private-key"));
  if (wrapped) {
    console.log('the key was carried base64-encoded, which is the shape that ' +
      'survives an environment variable');
  }
  const key = inspectPem(pem);
  console.log(`key: label=${key.label ?? 'none'} ` +
    `fingerprint=${key.fingerprint ?? 'none'} ` +
    `der=${key.der_bytes ?? '?'}B lines=${key.lines}`);
  console.log(`${key.state}: ${repairFor(key.state)}`);

  const iss = flag('--iss');
  if (iss !== null) {
    const form = issuerForm(iss);
    console.log(`iss form: ${form}`);
    if (form === 'unusable-issuer') {
      console.log('repair: iss must be the App\'s client ID or its numeric ' +
        'App ID. Anything else returns Integration not found.');
    }
  }

  let liveState = null;
  let identityState = null;
  if (!process.argv.includes('--offline')) {
    const jwt = (process.env.GITHUB_APP_JWT || "dummy-github-app-jwt");
    if (!jwt) {
      console.error('set GITHUB_APP_JWT to the JWT your signing code produces, ' +
        'or pass --offline to inspect the key only');
    } else {
      // The JWT is sent and nothing else. Never decoded, stored or logged.
      const res = await fetch(`${API}/app`, {
        headers: {
          Authorization: `Bearer ${jwt}`,
          Accept: 'application/vnd.github+json',
          'X-GitHub-Api-Version': '2022-11-28',
          'User-Agent': UA,
        },
      });
      let body = null;
      try { body = await res.json(); } catch { body = null; }
      const message = body && typeof body === 'object' ? body.message : null;
      console.log(`GET /app returned ${res.status}`);
      const [state, detail] = interpret(res.status, message);
      liveState = state;
      console.log(`${state}: ${detail}`);
      if (res.status === 200) {
        const [idState, idDetail] = reconcile(body, flag('--expect'));
        identityState = idState;
        console.log(`${idState}: ${idDetail}`);
      }
    }
  }

  console.log(JSON.stringify({
    label: key.label,
    fingerprint: key.fingerprint,
    der_bytes: key.der_bytes,
    lines: key.lines,
    key_state: key.state,
    live_state: liveState,
    identity_state: identityState,
  }, null, 2));
  const ok = usable(key.state)
    && (liveState === null || liveState === 'key-accepted')
    && identityState !== 'authenticated-as-another-app';
  process.exitCode = ok ? 0 : 1;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main() and set an exit code that fails a passing suite.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
