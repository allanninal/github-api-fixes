/**
 * Tell a route that does not exist from one that refuses your verb.
 *
 * Read only, and pointedly so. GitHub answers 404 rather than 405 for an
 * unsupported method, and this script will not settle that by sending the
 * method: several of the routes involved perform the operation on success,
 * and an unsupported one returns the same 404 you already have.
 *
 * Environment:
 *   GITHUB_TOKEN   optional read-only token; widens what the GET can see
 *   GITHUB_PATH    the path that 404s, e.g. /repos/o/r/topics
 *   GITHUB_VERB    the method your failing code sent, e.g. put
 *   GITHUB_ROOT    set to 1 to also read the root endpoint map
 */
const API = 'https://api.github.com';
const UA = 'github-route-or-verb/1.0';

/** The bare REST index GitHub degrades to when nothing was routed. */
export const DOCS_INDEX = 'https://docs.github.com/rest';

/** Verbs held lowercase throughout and upper-cased only for display. */
export const SAFE_VERBS = ['get', 'head'];

/** Routes people habitually call with the wrong verb. Not an API index. */
export const ROUTE_TABLE = [
  ['/user/starred/{owner}/{repo}', ['get', 'put', 'delete'],
    'check, star, unstar. Starring is a set operation, so it is PUT.'],
  ['/user/following/{username}', ['get', 'put', 'delete'],
    'check, follow, unfollow.'],
  ['/gists/{gist_id}/star', ['get', 'put', 'delete'], 'check, star, unstar.'],
  ['/repos/{owner}/{repo}', ['get', 'patch', 'delete'],
    'read, update, delete. Updating a repository is PATCH, not PUT.'],
  ['/repos/{owner}/{repo}/topics', ['get', 'put'],
    'read and replace. There is no POST: the whole list is set at once.'],
  ['/repos/{owner}/{repo}/merges', ['post'],
    'creation only. There is no GET here, so a GET probe cannot prove this route exists.'],
  ['/repos/{owner}/{repo}/subscription', ['get', 'put', 'delete'],
    'read, set, delete a watch.'],
  ['/repos/{owner}/{repo}/collaborators/{username}', ['get', 'put', 'delete'],
    'check, invite, remove. Adding a collaborator is PUT.'],
  ['/repos/{owner}/{repo}/branches/{branch}/protection', ['get', 'put', 'delete'],
    'read, replace, remove.'],
  ['/repos/{owner}/{repo}/pulls/{pull_number}', ['get', 'patch'],
    'read and update. Updating a pull request is PATCH.'],
  ['/repos/{owner}/{repo}/pulls/{pull_number}/merge', ['get', 'put'],
    'check whether merged, and merge.'],
  ['/repos/{owner}/{repo}/issues', ['get', 'post'], 'list and create.'],
  ['/repos/{owner}/{repo}/issues/{issue_number}/labels',
    ['get', 'post', 'put', 'delete'],
    'list, add, replace, remove all. POST adds; PUT replaces the set.'],
  ['/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches', ['post'],
    'creation only, and it has no GET.'],
  ['/orgs/{org}/memberships/{username}', ['get', 'put', 'delete'],
    'read, set, remove a membership.'],
];

/** REST requests this run will spend. Pure. */
export function readCost(withRoot) {
  return withRoot ? 2 : 1;
}

/** Would this script send that verb to find out. Pure. [state, detail]. */
export function probeRefusal(verb) {
  const name = String(verb ?? '').trim().toLowerCase();
  if (SAFE_VERBS.includes(name)) {
    return ['safe-to-send', `${name.toUpperCase()} does not change anything, so `
      + 'the probe is a reading.'];
  }
  return ['will-not-probe', `sending ${name.toUpperCase()} to confirm would be `
    + 'a write, and several routes here perform the operation on success. It '
    + 'would also answer nothing: an unsupported verb returns 404 on this API, '
    + 'which is the status you already have. The request costs a production '
    + 'change and returns no information.'];
}

/** The documentation_url in an error body, or null. Pure. */
export function documentationUrlOf(body) {
  if (!body || typeof body !== 'object') return null;
  const value = body.documentation_url;
  return (typeof value === 'string' && value) ? value : null;
}

/** Bare REST index, or a specific endpoint. Pure. [kind, detail]. */
export function docsUrlKind(url) {
  if (!url) {
    return ['absent', 'the body carried no documentation_url, so this reading '
      + 'cannot say whether a handler was reached.'];
  }
  let trimmed = String(url);
  while (trimmed.endsWith('/')) trimmed = trimmed.slice(0, -1);
  if (trimmed === DOCS_INDEX) {
    return ['generic', 'documentation_url is the bare REST index, so no handler '
      + 'was reached for this path and method.'];
  }
  if (trimmed.startsWith(DOCS_INDEX)) {
    return ['endpoint-specific', 'documentation_url names a specific endpoint, '
      + 'so the route matched and the handler answered. The resource is missing '
      + 'or hidden, which is a different note.'];
  }
  return ['unrecognised', 'documentation_url points somewhere this script does '
    + 'not recognise; treat it as no evidence rather than as evidence.'];
}

/** Sort the probe's answer. Pure. [state, detail]. */
export function classifyNotFound(status, body) {
  const code = Number(status) || 0;
  if (code === 200) {
    return ['route-answers-get', 'the same path answers a GET, so the path '
      + 'shape is right and nothing is hidden from this credential. A refusal '
      + 'on another verb is about the verb.'];
  }
  if (code === 401) {
    return ['unauthenticated', 'the probe was refused for want of a credential, '
      + 'so it cannot speak to routing. Re-run with a read-only token.'];
  }
  if (code === 403 || code === 429) {
    return ['refused-not-missing', 'a refusal is not a routing answer. Sort '
      + 'that 403 first; it has its own notes.'];
  }
  if (code !== 404) {
    return ['unexpected-status', `HTTP ${status} is neither a 404 nor a success, `
      + 'so there is nothing here to sort.'];
  }
  const [kind, detail] = docsUrlKind(documentationUrlOf(body));
  if (kind === 'endpoint-specific') return ['route-matched-resource-missing', detail];
  if (kind === 'generic') return ['nothing-routed-here', detail];
  return ['routing-unknown', detail];
}

/** Documented shape errors, checked locally. Pure. [state, detail]. */
export function pathShapeProblem(path) {
  const value = String(path ?? '');
  if (!value) return ['empty-path', 'no path was given.'];
  if (value.startsWith('http://') || value.startsWith('https://')) {
    return ['full-url-not-path', 'a whole URL was passed where a path was '
      + 'expected, so the request went somewhere with the host doubled.'];
  }
  if (!value.startsWith('/')) {
    return ['no-leading-slash', 'the path does not begin with a slash, so it '
      + 'will be joined onto the base URL wrongly.'];
  }
  const head = value.split('?')[0];
  if (head.includes('{') || head.includes('}')) {
    return ['placeholder-not-substituted', 'a template placeholder is still in '
      + 'the path. The request is asking for a repository literally named with '
      + 'braces.'];
  }
  if (head.slice(1).includes('//')) {
    return ['doubled-slash', 'the path contains an empty segment, usually an '
      + 'interpolated value that was empty. That is a different route from the '
      + 'one you meant.'];
  }
  if (head !== '/' && head.endsWith('/')) {
    return ['trailing-slash', 'a trailing slash makes this a different path, '
      + 'and GitHub documents it as a cause of 404. It is invisible in review.'];
  }
  if (head.includes(' ')) {
    return ['unencoded-space', 'an unencoded space in the path. URL-encode path '
      + 'parameters before interpolating them.'];
  }
  if (head.includes('\\')) {
    return ['backslash-in-path', 'a backslash in the path, usually a Windows '
      + 'path separator that leaked into a URL.'];
  }
  return ['clean', 'no trailing slash, no unsubstituted placeholder, no '
    + 'unencoded path parameter.'];
}

/** Match a concrete path against the table. Pure. [template, verbs, note]. */
export function matchRoute(path) {
  const head = String(path ?? '').split('?')[0];
  const parts = head.split('/').filter((p) => p !== '');
  for (const [template, verbs, note] of ROUTE_TABLE) {
    const wanted = template.split('/').filter((p) => p !== '');
    if (wanted.length !== parts.length) continue;
    let ok = true;
    for (let i = 0; i < wanted.length; i += 1) {
      const want = wanted[i];
      if (want.startsWith('{') && want.endsWith('}')) continue;
      if (want !== parts[i]) { ok = false; break; }
    }
    if (ok) return [template, verbs, note];
  }
  return [null, [], ''];
}

/** Is the verb documented for the route this path matches. Pure. */
export function verbVerdict(path, verb) {
  const name = String(verb ?? '').trim().toLowerCase();
  const [template, verbs, note] = matchRoute(path);
  if (template === null) {
    return ['route-not-in-table', 'this path matches no route in the table, '
      + 'which is a short list rather than an index of the API. Look the '
      + 'endpoint up and compare the verb by hand.'];
  }
  const shown = verbs.map((v) => v.toUpperCase()).join(', ');
  if (verbs.includes(name)) {
    return ['verb-is-documented', `${name.toUpperCase()} is a documented verb `
      + `for ${template} (${shown}), so the method is not your problem. ${note}`];
  }
  return ['verb-not-on-this-route', `you sent ${name.toUpperCase()}. ${template} `
    + `accepts ${shown}. ${note}`];
}

/** Can a GET prove this route exists. Pure. [state, detail]. */
export function getProbeIsEvidence(path) {
  const [template, verbs] = matchRoute(path);
  if (template === null) {
    return ['unknown-route', 'the route is not in the table, so whether a GET '
      + 'would prove anything is unknown.'];
  }
  if (verbs.includes('get')) {
    return ['probe-decides', `${template} has a documented GET, so a 200 from `
      + 'the probe settles the path shape.'];
  }
  return ['probe-cannot-decide', `${template} has no documented GET, so a bare `
    + '404 from the probe is expected and proves nothing. The table is the only '
    + 'evidence here.'];
}

/** Weak corroboration from x-accepted-github-permissions. Pure. */
export function permissionsHeaderHint(headers) {
  const bag = (headers && typeof headers === 'object') ? headers : {};
  for (const key of Object.keys(bag)) {
    if (key.toLowerCase() === 'x-accepted-github-permissions') {
      return ['permissions-were-evaluated', 'the response names an accepted '
        + 'permission, which means a handler looked at your credential. That '
        + 'points away from a routing problem. Corroboration only.'];
    }
  }
  return ['no-permission-header', 'no accepted-permission header came back. '
    + 'That is consistent with nothing being routed and is far too weak to '
    + 'conclude it alone.'];
}

/** Does the root endpoint map mention this path family. Pure. Coarse. */
export function rootMapCovers(root, path) {
  if (!root || typeof root !== 'object' || Object.keys(root).length === 0) {
    return ['root-unread', 'the root endpoint map was not read, so nothing '
      + 'corroborates the path family.'];
  }
  const parts = String(path ?? '').split('?')[0].split('/').filter((p) => p !== '');
  if (parts.length === 0) return ['no-path', 'there is no path to check against the map.'];
  const needle = `/${parts[0]}`;
  for (const value of Object.values(root)) {
    if (typeof value === 'string' && value.includes(needle)) {
      return ['family-known', `the root endpoint map contains ${needle}, so the `
        + 'first segment is a real family.'];
    }
  }
  return ['family-not-in-map', `the root endpoint map does not mention ${needle}. `
    + 'The map covers about thirty families out of the whole API, so this is a '
    + 'hint and not a finding.'];
}

/** The finding, in one state. Pure. [state, detail]. */
export function verdict(routingState, shapeState, verbState) {
  if (routingState === 'route-matched-resource-missing') {
    return ['resource-not-routing', 'the route matched and the handler '
      + 'answered. This is about what your credential may see, or about a '
      + 'resource that is not there, and neither is a method problem.'];
  }
  if (['unauthenticated', 'refused-not-missing', 'unexpected-status'].includes(routingState)) {
    return [routingState, 'the probe did not produce a routing answer, so '
      + 'nothing can be concluded about the verb from it.'];
  }
  if (shapeState !== 'clean') {
    return ['path-shape-wrong', 'the path itself is malformed, and that is a '
      + 'documented cause of 404 on this API. Fix the shape before looking at '
      + 'verbs.'];
  }
  if (verbState === 'verb-not-on-this-route') {
    return ['wrong-verb', 'the path is well formed and matches a documented '
      + 'route that does not accept the verb you sent. That is the 404.'];
  }
  if (routingState === 'route-answers-get' && verbState === 'verb-is-documented') {
    return ['route-and-verb-both-fine', 'the path answers a GET and your verb '
      + 'is documented for it, so the 404 you saw came from somewhere else '
      + 'entirely.'];
  }
  if (routingState === 'nothing-routed-here' && verbState === 'verb-is-documented') {
    return ['route-absent-or-wrong-host', 'nothing was routed, the path is well '
      + 'formed and the verb is documented for a route of that shape. Check '
      + 'that you are talking to the API host you think you are.'];
  }
  return ['undetermined', 'the readings do not settle it. Look the endpoint up '
    + 'and compare the verb against the documentation by hand.'];
}

/** The sentence a reader has to act on. Pure. Nothing here is sent. */
export function repair(state, path, verb) {
  const [, verbs] = matchRoute(path);
  if (state === 'wrong-verb') {
    const changing = verbs.filter((v) => !SAFE_VERBS.includes(v))
      .map((v) => v.toUpperCase()).join(' or ');
    return `send ${changing || 'the documented verb'} to this path instead of `
      + `${String(verb).toUpperCase()}. Nothing here sends it.`;
  }
  if (state === 'path-shape-wrong') {
    return 'fix the path before anything else: URL-encode the parameters, drop '
      + 'the trailing slash, and substitute every placeholder.';
  }
  if (state === 'resource-not-routing') {
    return 'stop looking at the method. Sort the 404 by what your credential '
      + 'can see; that has its own note.';
  }
  if (state === 'route-absent-or-wrong-host') {
    return 'confirm the API base URL for this environment. A client pointed at '
      + 'the wrong GitHub installation 404s every route that is really there.';
  }
  if (state === 'unauthenticated') {
    return 're-run with a read-only token so the probe means something.';
  }
  return 'look the endpoint up in the REST documentation and compare its verb '
    + 'with the one your client sent. Do not send the verb to find out.';
}

function headers(token) {
  const bag = {
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
  if (token) bag.Authorization = `Bearer ${token}`;
  return bag;
}

async function main() {
  const path = (process.env.GITHUB_PATH || "dummy-github-path");
  if (!path) {
    console.error('set GITHUB_PATH to the path that 404s');
    process.exitCode = 2;
    return;
  }
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const verb = (process.env.GITHUB_VER || "dummy-github-ver")B || 'get';
  const withRoot = (process.env.GITHUB_ROOT || "dummy-github-root") === '1';
  console.log(`read cost: ${readCost(withRoot)} REST request(s) against the core `
    + 'hourly quota');
  if (!token) {
    console.warn('no GITHUB_TOKEN: private paths will 404 for a third reason '
      + 'and the probe is weaker');
  }

  const [shapeState, shapeDetail] = pathShapeProblem(path);
  console.log(`path-shape: ${shapeState} - ${shapeDetail}`);

  const probe = await fetch(`${API}${path}`, { headers: headers(token) });
  console.log(`probe: GET ${path} -> HTTP ${probe.status}`);
  let body = null;
  try { body = await probe.json(); } catch { body = null; }
  const [routingState, routingDetail] = classifyNotFound(probe.status, body);
  console.log(`not-found: ${routingState} - ${routingDetail}`);

  const headerBag = {};
  probe.headers.forEach((value, key) => { headerBag[key] = value; });
  const [hintState, hintDetail] = permissionsHeaderHint(headerBag);
  console.log(`${hintState}: ${hintDetail}`);

  const [evidenceState, evidenceDetail] = getProbeIsEvidence(path);
  console.log(`${evidenceState}: ${evidenceDetail}`);

  const [verbState, verbDetail] = verbVerdict(path, verb);
  console.log(`${verbState}: ${verbDetail}`);

  const [refusalState, refusalDetail] = probeRefusal(verb);
  console.log(`${refusalState}: ${refusalDetail}`);

  let rootState = 'root-unread';
  let rootDetail = 'not read';
  if (withRoot) {
    const root = await fetch(`${API}/`, { headers: headers(token) });
    try {
      [rootState, rootDetail] = rootMapCovers(await root.json(), path);
    } catch {
      [rootState, rootDetail] = ['root-unread', 'the root map did not parse.'];
    }
    console.log(`${rootState}: ${rootDetail}`);
  }

  const [state, detail] = verdict(routingState, shapeState, verbState);
  console.log(`${state}: ${detail}`);
  const fix = repair(state, path, verb);
  console.log(`repair: ${fix}`);

  console.log(JSON.stringify({
    path,
    verb_sent: String(verb).toUpperCase(),
    probe_status: probe.status,
    documentation_url: documentationUrlOf(body),
    routing_state: routingState,
    path_shape_state: shapeState,
    verb_state: verbState,
    verb_detail: verbDetail,
    get_probe_evidence: evidenceState,
    permission_header_hint: hintState,
    root_map_state: rootState,
    probe_refusal: refusalState,
    probe_refusal_detail: refusalDetail,
    state,
    detail,
    repair: fix,
  }, null, 2));
  process.exitCode = ['wrong-verb', 'path-shape-wrong',
    'route-absent-or-wrong-host'].includes(state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
