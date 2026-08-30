/**
 * Tell a repository that went private apart from one that was deleted.
 *
 * Read only. GET requests and nothing else, and one of them carries no
 * credential at all: the method is a comparison between two callers reading the
 * same URL, so the anonymous request is made on purpose and never given an
 * Authorization header.
 *
 * Making a repository private removes anonymous access entirely, and GitHub
 * answers 404 rather than 403 so error codes cannot enumerate resources. A
 * client that read it anonymously for years sees exactly what it would see if
 * the repository had been deleted. One reading cannot separate those; two can.
 *
 * Environment:
 *   GITHUB_TOKEN    a token that still has access to the repository
 *   GITHUB_REPO     owner/name of the repository that started 404ing
 */
const API = 'https://api.github.com';
const UA = 'github-visibility-change/1.0';

/** The unauthenticated core quota, per IP address. Free to read. */
export const ANONYMOUS_CORE_LIMIT = 60;

/** The three values of visibility. The boolean is true for two of them. */
export const VISIBILITIES = ['public', 'private', 'internal'];

/** The classic scope that describes exactly what has stopped being true. */
export const BLIND_SCOPE = 'public_repo';
export const PRIVATE_SCOPE = 'repo';

/** What a fine-grained token needs instead, on that one repository. */
export const FINE_GRAINED_PERMISSIONS = ['Metadata: Read', 'Contents: Read'];

/** Billable requests this run spends against the core quota. Pure. */
export function readCost() {
  return 2;
}

/** Was this caller authenticated. Pure. */
export function clientIsAnonymous(coreLimit) {
  if (coreLimit === null || coreLimit === undefined || coreLimit === '') return null;
  const n = Number(coreLimit);
  if (!Number.isFinite(n)) return null;
  return n <= ANONYMOUS_CORE_LIMIT;
}

/** The three-valued visibility, falling back to the boolean. Pure. */
export function visibilityOf(repo) {
  const r = repo || {};
  const value = String(r.visibility ?? '').trim().toLowerCase();
  if (VISIBILITIES.includes(value)) return value;
  if (r.private === true) return 'private';
  if (r.private === false) return 'public';
  return 'unreported';
}

/** Read x-oauth-scopes into a list, keeping absent and empty apart. Pure. */
export function scopeList(headerValue) {
  if (headerValue === null || headerValue === undefined) return null;
  return String(headerValue).split(',').map((s) => s.trim()).filter(Boolean);
}

/** Is the client's scope set exactly the wrong shape for this. Pure. */
export function scopeGap(scopes, visibility) {
  if (visibility === 'public') {
    return ['not-applicable', 'the repository is public, so no scope is '
      + 'required to read it.'];
  }
  if (scopes === null || scopes === undefined) {
    return ['no-scopes-reported', 'this credential reports no OAuth scopes, so '
      + `it is a fine-grained or App token. It needs ${FINE_GRAINED_PERMISSIONS.join(', ')} `
      + 'on this repository, granted by the owner.'];
  }
  if (scopes.includes(PRIVATE_SCOPE)) {
    return ['scope-sufficient', `the token carries '${PRIVATE_SCOPE}', which `
      + 'covers a private repository. If it still cannot read this one, the '
      + 'account behind it has no grant on the repository.'];
  }
  if (scopes.includes(BLIND_SCOPE)) {
    return ['blind-scope', `the token carries '${BLIND_SCOPE}' and not `
      + `'${PRIVATE_SCOPE}'. That scope grants every public repository and no `
      + 'private one, so it is exactly as blind here as sending no token at all.'];
  }
  return ['scope-insufficient', `the token carries ${scopes.join(', ') || 'no scopes at all'}, `
    + `none of which reaches a private repository. It needs '${PRIVATE_SCOPE}'.`];
}

/** Sort a pair of readings of one URL. Pure. [state, detail]. */
export function classify(anonStatus, authStatus, repo = null) {
  const visibility = visibilityOf(repo);
  if (String(anonStatus) === '301' || String(authStatus) === '301') {
    return ['moved', 'a 301 means the repository was renamed or transferred and '
      + 'a redirect was left behind. That is a different note; follow it once '
      + 'and rewrite your configuration.'];
  }
  if (authStatus === 200 && anonStatus === 404) {
    if (visibility === 'internal') {
      return ['internal-visibility', 'the repository is internal: private=true, '
        + 'but readable by every member of the enterprise rather than by a named '
        + 'list. A client keying on the private boolean cannot see that '
        + 'difference, and the repair for it is membership rather than a '
        + 'repository grant.'];
    }
    return ['went-private', 'the repository is readable with a token and '
      + 'invisible without one, so it exists and is no longer public. Deletion '
      + 'would answer 404 to both readings.'];
  }
  if (authStatus === 200 && anonStatus === 200) {
    return ['still-public', 'both readings succeeded, so visibility is not what '
      + 'broke. The 404 your client recorded has another cause.'];
  }
  if (authStatus === 404 && anonStatus === 404) {
    return ['invisible-to-both', 'neither reading can see it, so this is '
      + 'deletion or an account that was never granted access. That is the wider '
      + '404 triage and not this note.'];
  }
  if (authStatus !== 200 && anonStatus === 200) {
    return ['token-is-the-problem', 'the anonymous read succeeded and the '
      + 'authenticated one did not, so the repository is public and the '
      + 'credential is failing. Check whether the token is expired or revoked.'];
  }
  return ['unclassified', `authenticated ${authStatus} and anonymous `
    + `${anonStatus} is not a combination this sorts. Report both codes before `
    + 'drawing a conclusion.'];
}

/** The second, slower failure this change produces. Pure, or null. */
export function forkFallout(repo) {
  const r = repo || {};
  if (visibilityOf(r) === 'public') return null;
  if ((r.forks_count || 0) <= 0) return null;
  return 'forks that existed while it was public were split into their own '
    + 'network and are still public, so a link that still resolves may be a copy '
    + 'that stopped receiving commits.';
}

/** What this cannot establish, said out loud. Pure. */
export function blindSpot() {
  return 'no visibility-changed timestamp is exposed to a reader, and the audit '
    + 'log that records it needs organization-level access. When it happened is '
    + 'in your own logs, not in this response.';
}

/** The sentence a reader has to act on. Pure. */
export function repair(state, scopeState = null) {
  const credential = `give the client the '${PRIVATE_SCOPE}' scope (classic) or `
    + `${FINE_GRAINED_PERMISSIONS.join(' and ')} on this repository `
    + '(fine-grained), granted by the owner.';
  if (state === 'went-private') {
    let text = credential;
    if (scopeState === 'blind-scope') {
      text += ` The scope it holds now, '${BLIND_SCOPE}', covers exactly what `
        + 'has stopped being true.';
    }
    return text;
  }
  if (state === 'internal-visibility') {
    return 'the repository is internal, so access follows enterprise membership. '
      + 'A machine account has to be a member of the enterprise, and after that '
      + credential;
  }
  if (state === 'invisible-to-both') {
    return 'stop here and run the wider 404 triage. Nothing about visibility can '
      + 'be established when no credential can see it.';
  }
  if (state === 'still-public') {
    return 'look elsewhere. The repository is public and readable anonymously, '
      + 'so the 404 came from something other than visibility.';
  }
  if (state === 'token-is-the-problem') {
    return 'check the credential rather than the repository. An expired or '
      + 'revoked token authenticates as nobody.';
  }
  if (state === 'moved') {
    return 'follow the redirect once, take full_name from the response, and '
      + 'store the repository id so the next rename is not a surprise either.';
  }
  return 'report both status codes before drawing a conclusion.';
}

const COMMON = {
  Accept: 'application/vnd.github+json',
  'X-GitHub-Api-Version': '2022-11-28',
  'User-Agent': UA,
};

async function coreLimit(headers) {
  const res = await fetch(`${API}/rate_limit`, { headers });
  if (!res.ok) return null;
  try {
    const body = await res.json();
    return ((body.resources || {}).core || {}).limit ?? null;
  } catch { return null; }
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const repoName = (process.env.GITHUB_REPO || "dummy-github-repo");
  if (!token || !repoName) {
    console.error('set GITHUB_TOKEN (read-only with access) and GITHUB_REPO');
    process.exitCode = 2;
    return;
  }
  console.log(`read cost: ${readCost()} request(s) against the core hourly quota, `
    + `plus 2 free /rate_limit calls. One read is anonymous and is billed to the `
    + `unauthenticated bucket for this IP address, which is ${ANONYMOUS_CORE_LIMIT} an hour.`);

  const authedHeaders = { ...COMMON, Authorization: `Bearer ${token}` };
  // Deliberately credential-free, and it must stay that way.
  const anonHeaders = { ...COMMON };

  const authLimit = await coreLimit(authedHeaders);
  const anonLimit = await coreLimit(anonHeaders);

  const authRes = await fetch(`${API}/repos/${repoName}`,
    { headers: authedHeaders, redirect: 'manual' });
  const anonRes = await fetch(`${API}/repos/${repoName}`,
    { headers: anonHeaders, redirect: 'manual' });
  console.log(`authenticated: HTTP ${authRes.status}  core.limit=${authLimit}`);
  console.log(`anonymous:     HTTP ${anonRes.status}  core.limit=${anonLimit}`);

  let repo = null;
  const scopes = scopeList(authRes.headers.get('x-oauth-scopes'));
  if (authRes.status === 200) {
    repo = await authRes.json();
    console.log(`${repoName}: private=${repo.private} visibility=${visibilityOf(repo)}`);
  }

  const [state, detail] = classify(anonRes.status, authRes.status, repo);
  console.log(`${state}: ${detail}`);
  const [scopeState, scopeDetail] = scopeGap(scopes, visibilityOf(repo));
  console.log(`${scopeState}: ${scopeDetail}`);
  const fallout = forkFallout(repo);
  if (fallout) console.log(`forks-note: ${fallout}`);
  console.log(`blind-spot: ${blindSpot()}`);
  console.log(`repair: ${repair(state, scopeState)}`);

  console.log(JSON.stringify({
    repository: repoName,
    authenticated_status: authRes.status,
    anonymous_status: anonRes.status,
    authenticated_core_limit: authLimit,
    anonymous_core_limit: anonLimit,
    client_was_anonymous: clientIsAnonymous(authLimit),
    private: (repo || {}).private ?? null,
    visibility: visibilityOf(repo),
    scopes,
    state,
    detail,
    scope_state: scopeState,
    forks_note: fallout,
    blind_spot: blindSpot(),
    repair: repair(state, scopeState),
  }, null, 2));
  process.exitCode = ['went-private', 'internal-visibility'].includes(state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
