/**
 * Work out which permission a fine-grained token is missing.
 *
 * Read only. Every call is a GET except the single GraphQL query, and the
 * GraphQL endpoint takes its document in the request body, so a read travels
 * by POST there exactly as a write would. The document is parsed first and
 * refused if it contains a mutation or a subscription.
 *
 * A fine-grained personal access token carries per-resource permissions rather
 * than scopes. The refusing response names what the endpoint accepts in
 * x-accepted-github-permissions, and nothing anywhere names what the token
 * holds: fine-grained tokens send no x-oauth-scopes at all. So one half of the
 * diff is read and the other is measured, one cheap request per permission.
 *
 * Environment:
 *   GITHUB_TOKEN   the fine-grained token you are diagnosing
 *   GITHUB_REPO    owner/name to probe
 */
const API = 'https://api.github.com';
const UA = 'github-fine-grained-pat-probe/1.0';

export const POINTS_PER_QUERY = 1;

/** Token prefixes GitHub documents. Only the prefix is ever printed. */
export const TOKEN_PREFIXES = [
  ['github_pat_', 'fine-grained personal access token'],
  ['ghp_', 'classic personal access token'],
  ['gho_', 'OAuth user token'],
  ['ghu_', 'GitHub App user-to-server token'],
  ['ghs_', 'GitHub App installation token'],
  ['ghr_', 'GitHub App refresh token'],
];

/** One cheap read per fine-grained permission. */
export const PROBES = [
  ['metadata', '/repos/{owner}/{repo}', 'Metadata'],
  ['contents', '/repos/{owner}/{repo}/contents/', 'Contents'],
  ['issues', '/repos/{owner}/{repo}/issues?per_page=1', 'Issues'],
  ['pull_requests', '/repos/{owner}/{repo}/pulls?per_page=1', 'Pull requests'],
  ['actions', '/repos/{owner}/{repo}/actions/workflows?per_page=1', 'Actions'],
];

const ISSUES_QUERY = 'query($owner: String!, $name: String!) {'
  + ' repository(owner: $owner, name: $name) {'
  + ' issues(first: 1) { totalCount } } }';

/** Remove GraphQL comments and string literals from a document. Pure. */
export function stripNoise(document) {
  const src = String(document ?? '');
  const out = [];
  let i = 0;
  while (i < src.length) {
    const ch = src[i];
    if (ch === '#') {
      while (i < src.length && src[i] !== '\n') i += 1;
      continue;
    }
    if (src.startsWith('"""', i)) {
      const j = src.indexOf('"""', i + 3);
      i = j < 0 ? src.length : j + 3;
      out.push(' ');
      continue;
    }
    if (ch === '"') {
      i += 1;
      while (i < src.length && src[i] !== '"') i += src[i] === '\\' ? 2 : 1;
      i += 1;
      out.push(' ');
      continue;
    }
    out.push(ch);
    i += 1;
  }
  return out.join('');
}

/** The top-level operations in a document, in order. Pure. */
export function operations(document) {
  const src = `${stripNoise(document)} `;
  const ops = [];
  let depth = 0;
  let word = '';
  let declared = null;
  for (const ch of src) {
    if (/[A-Za-z0-9_]/.test(ch)) { word += ch; continue; }
    if (word) {
      if (depth === 0 && ['query', 'mutation', 'subscription', 'fragment'].includes(word)) {
        declared = word;
      }
      word = '';
    }
    if (ch === '{') {
      if (depth === 0) { ops.push(declared || 'query'); declared = null; }
      depth += 1;
    } else if (ch === '}') {
      depth = Math.max(0, depth - 1);
    }
  }
  return ops;
}

/** Why this document will not be sent, or null if it is a read. Pure. */
export function refusal(document) {
  const ops = operations(document);
  if (ops.length === 0) return 'the document contains no operation to send.';
  for (const kind of ['mutation', 'subscription']) {
    if (ops.includes(kind)) {
      return `the document contains a ${kind}. This script sends queries only: `
        + 'a query is a read, and the section it belongs to promises its '
        + 'scripts never write.';
    }
  }
  return null;
}

/** The credential type named by the token's prefix. Pure. */
export function tokenKind(token) {
  const text = String(token ?? '');
  for (const [prefix, label] of TOKEN_PREFIXES) {
    if (text.startsWith(prefix)) return label;
  }
  return 'unrecognised credential';
}

/** The prefix alone, safe to print. Pure. */
export function tokenPrefix(token) {
  const text = String(token ?? '');
  for (const [prefix] of TOKEN_PREFIXES) {
    if (text.startsWith(prefix)) return prefix;
  }
  return 'none';
}

/** Whether x-oauth-scopes arrived, and empty or not. Pure. */
export function scopeHeaderState(headers) {
  if (!headers || typeof headers !== 'object') return 'absent';
  for (const [key, value] of Object.entries(headers)) {
    if (String(key).toLowerCase() === 'x-oauth-scopes') {
      return String(value).trim() === '' ? 'present-empty' : 'present';
    }
  }
  return 'absent';
}

/** What credential this is, from the prefix and the header. Pure. */
export function identify(token, headers) {
  const kind = tokenKind(token);
  const state = scopeHeaderState(headers);
  const fineGrained = kind.startsWith('fine-grained');
  if (fineGrained && state === 'absent') {
    return [kind, `prefix ${tokenPrefix(token)} and no x-oauth-scopes header, `
      + 'which a classic token always sends even when it is empty.'];
  }
  if (fineGrained) {
    return [kind, 'prefix says fine-grained but an x-oauth-scopes header '
      + 'arrived, which fine-grained tokens do not send. Check that the header '
      + 'came from a call made with this token.'];
  }
  if (state === 'present' || state === 'present-empty') {
    return [kind, 'an x-oauth-scopes header arrived, so this credential carries '
      + 'scopes rather than fine-grained permissions.'];
  }
  return [kind, 'no x-oauth-scopes header and no fine-grained prefix.'];
}

/** Parse x-accepted-github-permissions into alternatives. Pure. */
export function parseAcceptedPermissions(value) {
  const out = [];
  for (const alternative of String(value ?? '').split(',')) {
    const pairs = [];
    for (const raw of alternative.split(';')) {
      const clause = raw.trim();
      if (!clause) continue;
      const idx = clause.indexOf('=');
      const name = idx < 0 ? clause : clause.slice(0, idx);
      const level = idx < 0 ? '' : clause.slice(idx + 1);
      pairs.push([name.trim(), level.trim() || 'read']);
    }
    if (pairs.length) out.push(pairs);
  }
  return out;
}

/** Which credential the refusal blames. Pure. */
export function actorFromMessage(message) {
  const text = String(message ?? '').toLowerCase();
  if (text.includes('personal access token')) return 'fine-grained-pat';
  if (text.includes('by integration')) return 'github-app';
  if (text.includes('oauth app') || text.includes('oauth application')) return 'oauth-app';
  return null;
}

/** What one probe proves about one permission. Pure. [verdict, why]. */
export function grantFromProbe(status, message) {
  const code = Number(status);
  if (!Number.isFinite(code) || status === null || status === '') {
    return ['error', 'no status to read.'];
  }
  if (code >= 200 && code < 300) {
    return ['granted', 'the read succeeded, so this permission is held.'];
  }
  if (code === 403 && actorFromMessage(message) === 'fine-grained-pat') {
    return ['refused', '403 naming the personal access token, so this '
      + 'permission is not held.'];
  }
  if (code === 403) {
    return ['refused-other', '403 that does not name a personal access token. '
      + 'Read the message: another actor or another rule refused this.'];
  }
  if (code === 404) {
    return ['ambiguous', 'a 404 can hide a 403; see /github/404-masking-403/ '
      + 'before concluding anything from this row.'];
  }
  if (code === 401) {
    return ['unauthenticated', 'the token itself was rejected, which is a '
      + 'credential problem rather than a permission one.'];
  }
  return ['error', `HTTP ${code}, which is neither a grant nor a refusal.`];
}

/** Judge one refusal. Pure. Returns [state, detail]. */
export function classify(status, message, headers, token, orgOnly = false) {
  const [kind] = identify(token, headers);
  const actor = actorFromMessage(message);
  const code = Number(status) || 0;
  if (code >= 200 && code < 300) return ['clean', 'this call was not refused.'];
  if (actor === 'github-app') {
    return ['not-this-note-app', 'the message names an integration, so this is '
      + 'a GitHub App installation token and its permissions are readable '
      + 'through GET /app.'];
  }
  if (actor === 'oauth-app') {
    return ['not-this-note-oauth-app', 'the message names an OAuth App, so the '
      + 'organization is restricting the App rather than the token lacking a '
      + 'permission.'];
  }
  if (code === 404) {
    return ['ambiguous-404', 'a 404 rather than a 403, which GitHub uses to '
      + 'avoid confirming that a private resource exists.'];
  }
  if (actor === 'fine-grained-pat' && orgOnly) {
    return ['org-resource-refused', 'every repository probe passed and only '
      + 'organization resources were refused, which is more often a pending '
      + 'approval or an organization token policy than a missing permission.'];
  }
  if (actor === 'fine-grained-pat') {
    const wanted = parseAcceptedPermissions(
      (headers || {})['x-accepted-github-permissions'] || '');
    const named = wanted
      .map((alt) => alt.map(([n, l]) => `${n}=${l}`).join(', '))
      .join(' or ') || 'nothing the response named';
    return ['fine-grained-permission-missing',
      `the endpoint accepts ${named} and this token does not hold it.`];
  }
  if (!kind.startsWith('fine-grained')) {
    return ['not-this-note-classic', 'this credential carries scopes rather '
      + 'than fine-grained permissions, so the two scope headers answer it '
      + 'directly.'];
  }
  return ['unclassified', 'a refusal whose message names no actor. Log it '
    + 'verbatim rather than guessing which credential was blamed.'];
}

/** Errors in a GraphQL response that blame the personal access token. Pure. */
export function graphqlPatRefusals(body) {
  if (!body || typeof body !== 'object' || !Array.isArray(body.errors)) return [];
  const out = [];
  for (const err of body.errors) {
    if (!err || typeof err !== 'object') continue;
    if (actorFromMessage(err.message) === 'fine-grained-pat') {
      const path = (err.path || []).map((p) => String(p)).join('.') || '(no path)';
      out.push([path, String(err.message ?? '')]);
    }
  }
  return out;
}

/** Where to read what the endpoint wanted, per API. Pure. */
export function whereTheRequirementLives(protocol) {
  if (String(protocol).toLowerCase() === 'graphql') {
    return 'nowhere on this response. GraphQL refusals carry no '
      + 'x-accepted-github-permissions header, so make the equivalent REST call '
      + 'and read it off that refusal instead.';
  }
  return 'the x-accepted-github-permissions header on the refusing response itself.';
}

/** Permissions the endpoint named that the probes show are not held. Pure. */
export function missingPermissions(headers, grants) {
  const wanted = parseAcceptedPermissions(
    (headers || {})['x-accepted-github-permissions'] || '');
  const missing = [];
  for (const alternative of wanted) {
    for (const [name, level] of alternative) {
      const held = (grants || {})[name];
      if (held === 'refused' || held === undefined) missing.push([name, level]);
    }
  }
  return missing;
}

/** The sentence a reader has to act on. Pure. */
export function repair(state, headers = null) {
  if (state === 'fine-grained-permission-missing') {
    const wanted = parseAcceptedPermissions(
      (headers || {})['x-accepted-github-permissions'] || '');
    const named = wanted.flat().map(([n, l]) => `${n}=${l}`).join(', ');
    return `add ${named || 'the permission the header names'} to this token's `
      + 'repository permissions -- exactly what x-accepted-github-permissions '
      + 'named, and nothing else.';
  }
  if (state === 'org-resource-refused') {
    return 'check whether an organization owner still has to approve this '
      + 'token, and whether the organization allows fine-grained tokens at all. '
      + 'No permission you tick takes effect first.';
  }
  if (state === 'not-this-note-app') {
    return 'see /github/app-permission-missing/ -- an App\'s permissions are '
      + 'readable and adding one needs every installation to accept the upgrade.';
  }
  if (state === 'not-this-note-classic') {
    return 'see /github/missing-oauth-scope/ -- both halves of that diff arrive '
      + 'as headers on the same response.';
  }
  if (state === 'ambiguous-404') {
    return 'see /github/404-masking-403/ -- decide between missing and '
      + 'invisible before changing any permission.';
  }
  if (state === 'clean') {
    return 'nothing on this call. Run the probe matrix anyway if you want to '
      + 'know what the token can reach before it matters.';
  }
  return 'record the status, the message and the x-accepted-github-permissions '
    + 'header verbatim; between them they name the actor and the requirement.';
}

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'Content-Type': 'application/json',
    'User-Agent': UA,
  };
}

function headerObject(res) {
  const out = {};
  res.headers.forEach((value, key) => { out[key.toLowerCase()] = value; });
  return out;
}

async function runQuery(token, document, variables) {
  const res = await fetch(`${API}/graphql`, {
    // A GraphQL query is a read. POST is only how the document reaches the
    // endpoint, and refusal() has already rejected anything that is not a read.
    method: 'POST',
    headers: headers(token),
    body: JSON.stringify({ query: document, variables: variables || {} }),
  });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return { status: res.status, body };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const repo = (process.env.GITHUB_REPO || "dummy-github-repo");
  if (!token || !repo) {
    console.error('set GITHUB_TOKEN (the token you are diagnosing) and GITHUB_REPO=owner/name');
    process.exitCode = 2;
    return;
  }
  const [owner, name] = repo.split('/');
  const whyNot = refusal(ISSUES_QUERY);
  if (whyNot) {
    console.error(`refusing to send: ${whyNot}`);
    process.exitCode = 2;
    return;
  }
  console.log(`cost: ${PROBES.length + 1} core request(s) out of 5,000/hour, `
    + `plus ${POINTS_PER_QUERY} GraphQL point`);

  const who = await fetch(`${API}/user`, { headers: headers(token) });
  const [kind, detail] = identify(token, headerObject(who));
  console.log(`credential: ${kind}`);
  console.log(`  ${detail}`);

  const grants = {};
  const rows = [];
  let refusalHeaders = {};
  let refusalMessage = '';
  let refusalStatus = 0;
  for (const [permission, path, label] of PROBES) {
    const url = API + path.replace('{owner}', owner).replace('{repo}', name);
    // eslint-disable-next-line no-await-in-loop
    const res = await fetch(url, { headers: headers(token) });
    let message = '';
    if (res.status >= 400) {
      // eslint-disable-next-line no-await-in-loop
      try { message = String(((await res.json()) || {}).message || ''); } catch { message = ''; }
    }
    const [verdict, why] = grantFromProbe(res.status, message);
    grants[permission] = verdict;
    const all = headerObject(res);
    const accepted = all['x-accepted-github-permissions'] || '';
    if (verdict === 'refused' && !refusalMessage) {
      refusalHeaders = all;
      refusalMessage = message;
      refusalStatus = res.status;
    }
    console.log(`${permission.padEnd(14)} ${res.status}  ${verdict.padEnd(10)} `
      + `${accepted ? `x-accepted-github-permissions: ${accepted}` : why}`);
    rows.push({ permission, settings_label: label, status: res.status, verdict, accepted });
  }

  const [state, why] = classify(refusalStatus, refusalMessage, refusalHeaders, token);
  console.log(`${state}: ${why}`);
  console.log(`the requirement lives in ${whereTheRequirementLives('rest')}`);

  const { status, body } = await runQuery(token, ISSUES_QUERY, { owner, name });
  const gql = graphqlPatRefusals(body);
  console.log(`graphql: HTTP ${status}, ${gql.length} refusal(s) naming the `
    + 'personal access token');
  for (const [path, message] of gql) console.log(`  path=${path}  ${message}`);
  if (gql.length) {
    console.log(`through graphql the requirement lives ${whereTheRequirementLives('graphql')}`);
  }
  console.log(`repair: ${repair(state, refusalHeaders)}`);

  console.log(JSON.stringify({
    credential: kind,
    prefix: tokenPrefix(token),
    probes: rows,
    missing_permissions: missingPermissions(refusalHeaders, grants),
    graphql_refusals: gql,
    state,
    detail: why,
  }, null, 2));
  process.exitCode = state.startsWith('fine-grained') || state.startsWith('org-resource') ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
