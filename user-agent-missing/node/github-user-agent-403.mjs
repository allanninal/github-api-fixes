/**
 * Sort a GitHub 403 by cause, then grade the User-Agent the client sent.
 *
 * Read only. One GET, defaulting to the REST root, which any anonymous caller
 * may read. The credential is optional, because the rule this note is about is
 * applied before the credential is looked at.
 *
 * One honest difference from the Python version: Node's fetch supplies a
 * default User-Agent of its own when you omit the header, so the reproduction
 * flag here cannot actually produce a request with no header. What this script
 * grades is the header it configured, which is what your code controls.
 */
const API = 'https://api.github.com';

/** The User-Agent strings HTTP clients supply when nobody sets one. */
export const LIBRARY_DEFAULTS = [
  'python-requests/', 'python-urllib/', 'urllib3/', 'python-httpx/', 'httpx/',
  'go-http-client/', 'node-fetch/', 'undici', 'node/', 'axios/', 'got (',
  'okhttp/', 'java/', 'apache-httpclient/', 'curl/', 'libcurl/', 'wget/',
  'httpie/', 'postmanruntime/', 'restsharp/', 'guzzlehttp/', 'faraday',
  'ruby/', 'php/', 'dart/', 'reqwest/', 'http.rb/', 'python/',
];

/** True when some token in the string looks like name/1.2. Pure. */
function hasVersion(text) {
  for (const part of String(text).split(/\s+/)) {
    if (part.includes('/')) {
      const tail = part.slice(part.indexOf('/') + 1).replace(/^[vV]+/, '');
      if (tail.length && tail[0] >= '0' && tail[0] <= '9') return true;
    }
  }
  return false;
}

/** Grade a User-Agent string. Pure. Five grades, not two. */
export function gradeUserAgent(value) {
  if (value === null || value === undefined) {
    return ['absent',
      'no User-Agent header at all. GitHub refuses the request before it ' +
      'considers the credential, so this fails on endpoints that need no ' +
      'credential.'];
  }
  const text = String(value).trim();
  if (!text) {
    return ['empty',
      'the header is present with an empty value, which is refused exactly ' +
      'as if it had never been set.'];
  }
  const low = text.toLowerCase();
  for (const prefix of LIBRARY_DEFAULTS) {
    if (low.startsWith(prefix)) {
      return ['library-default',
        'the header names the HTTP library rather than your integration. The ' +
        'request works; nobody at GitHub can tell your traffic from anyone ' +
        "else's using that library."];
    }
  }
  const version = hasVersion(text);
  const contact = low.includes('http') || text.includes('@');
  if (version && contact) {
    return ['descriptive',
      'names the application, a version and a way to reach you. Nothing to change.'];
  }
  if (version || contact) {
    return ['named',
      'identifies the caller, but only halfway. Add whichever half is ' +
      'missing: a version, or a URL or address to reach you at.'];
  }
  return ['opaque',
    'present and custom, but it names nothing anyone could act on. Add a ' +
    'version and a contact.'];
}

/** Sort a 403 into the four things it means on this API. Pure. */
export function classify403(message, headers) {
  const text = String(message ?? '').toLowerCase();
  const head = {};
  for (const [k, v] of Object.entries(headers ?? {})) head[String(k).toLowerCase()] = String(v);
  if (text.includes('user-agent') || text.includes('administrative rules')) {
    return ['user-agent-rule',
      'the body names the rule: GitHub requires a User-Agent header on every ' +
      'API request and refuses the ones that arrive without it.'];
  }
  if (text.includes('secondary rate limit') || text.includes('abuse detection')) {
    return ['secondary-rate-limit',
      'a secondary limit, which is about the shape of the traffic rather ' +
      'than the number of requests. Slow down and honour retry-after; no ' +
      'header changes this.'];
  }
  if (head['x-ratelimit-remaining'] === '0') {
    return ['primary-rate-limit',
      'x-ratelimit-remaining is zero, so this is the hourly quota and the ' +
      'reset time is on the same response.'];
  }
  if (text.includes('saml') || text.includes('single sign-on') || text.includes('sso')) {
    return ['sso-enforcement',
      'an organization enforcing SSO is hiding the resource from a ' +
      'credential that has not been authorized for it.'];
  }
  if (text.includes('not accessible by integration') || text.includes('must have admin')
      || text.includes('resource not accessible') || text.includes('permission')) {
    return ['permission',
      'an authorization refusal: the credential reached GitHub, was ' +
      'accepted, and is not allowed to do this.'];
  }
  if (text.includes('ip address') || text.includes('allow list') || text.includes('allowlist')) {
    return ['ip-allow-list',
      'an organization IP allow list refused the source address. The repair ' +
      'is a network conversation, not a code change.'];
  }
  return ['unclassified-403',
    'the body does not match any of the shapes this script knows. Read it ' +
    'literally; it is the most specific thing you have.'];
}

/** Combine a status, a body message and what the client actually sent. Pure. */
export function verdict(status, message, headers, userAgentSent) {
  const [grade, detail] = gradeUserAgent(userAgentSent);
  if (status === 403) {
    const [cause, why] = classify403(message, headers);
    if (cause === 'user-agent-rule') {
      const shown = (grade === 'absent' || grade === 'empty')
        ? 'nothing' : JSON.stringify(userAgentSent);
      return ['user-agent-missing', `${why} What the client actually sent: ${shown}.`];
    }
    return [cause, `${why} This is a 403, but not the one this page is ` +
      'about, and no User-Agent will repair it.'];
  }
  if (status === 401) {
    return ['not-a-user-agent-problem',
      'a 401 means a credential was received and refused, or was required ' +
      'and never arrived. The User-Agent rule answers 403 and never 401.'];
  }
  if (status >= 400) {
    return ['other-failure',
      `status ${status}, which the User-Agent rule does not produce. The ` +
      `header that was sent grades as ${grade}.`];
  }
  if (grade === 'descriptive' || grade === 'named') {
    return ['user-agent-ok',
      `the request succeeded and the header identifies the caller. ${detail}`];
  }
  return ['identifiable-agent-missing',
    `the request succeeded, so the rule itself is satisfied, but ${detail}`];
}

/** Build the replacement header value. Pure. */
export function suggestUserAgent(app, version = '1.0', contact = null) {
  let slug = String(app).toLowerCase().split('').map(
    (c) => (/[a-z0-9]/.test(c) ? c : '-')).join('');
  while (slug.includes('--')) slug = slug.replaceAll('--', '-');
  slug = slug.replace(/^-+|-+$/g, '') || 'unnamed-integration';
  let agent = `${slug}/${version}`;
  if (contact) agent += ` (+${contact})`;
  return agent;
}

function arg(name, fallback) {
  const i = process.argv.indexOf(name);
  return i === -1 ? fallback : process.argv[i + 1];
}

async function main() {
  const path = arg('--path', '/');
  const app = arg('--app', '');
  const contact = arg('--contact', '');
  const strip = process.argv.includes('--no-user-agent');

  const headers = {
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
  };
  if (!strip && app) headers['User-Agent'] = suggestUserAgent(app, '1.0', contact || null);

  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  } else {
    console.log('no GITHUB_TOKEN set, which is fine: the User-Agent rule is ' +
      'applied before authentication, so an anonymous request demonstrates it ' +
      'exactly as well');
  }

  const url = path.startsWith('/') ? API + path : path;
  const res = await fetch(url, { headers });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  const message = body && typeof body === 'object' ? body.message ?? null : null;
  const seen = {};
  for (const [k, v] of res.headers.entries()) seen[k.toLowerCase()] = v;

  // What this script configured, which is the part your code controls. Node
  // adds a default of its own when the key is absent, so an absent key here
  // does not mean an absent header on the wire.
  const sent = headers['User-Agent'] ?? null;

  console.log(`${path} returned ${res.status}`);
  console.log(`user-agent configured: ${sent ?? 'none, so Node supplied its own'}`);
  console.log(`body message:          ${message ?? 'none'}`);
  console.log(`remaining quota:       ${seen['x-ratelimit-remaining'] ?? 'not reported'}`);

  const [state, detail] = verdict(res.status, message, seen, sent);
  console.log(`${state}: ${detail}`);

  if (state === 'user-agent-missing' || state === 'identifiable-agent-missing') {
    const want = suggestUserAgent(app || 'your integration', '1.0',
      contact || 'https://example.com/contact');
    console.log('repair: set this once on the client or transport, never per ' +
      `request: User-Agent: ${want}`);
  }

  console.log(JSON.stringify({ path, status: res.status, userAgentSent: sent, message, state }, null, 2));
  process.exitCode = (state === 'user-agent-missing' ||
    state === 'identifiable-agent-missing') ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not fire a live request and set an exit code the suite then inherits.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
