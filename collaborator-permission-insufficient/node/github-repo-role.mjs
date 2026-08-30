/**
 * Read an account's role on one repository instead of provoking a 403.
 *
 * Read only. GET requests and nothing else. The note is about writes being
 * refused and this script never attempts one: the role arrives on an ordinary
 * repository read, the minimum role for each action is documented, and the
 * comparison between them happens locally.
 *
 * A scope bounds what a token may do on the account's behalf and cannot grant
 * the account access it does not have, so a token carrying `repo` held by an
 * account with read on that repository is powerless there.
 *
 * Environment:
 *   GITHUB_TOKEN    a token with read access to the repository
 *   GITHUB_REPO     owner/name
 *   GITHUB_ACTION   the action being refused, default merge-pull-request
 *   GITHUB_USER     report this account's role instead of the token's own
 */
const API = 'https://api.github.com';
const UA = 'github-repo-role/1.0';

/** Weakest first. Every role implies the ones below it. */
export const ROLES = ['none', 'read', 'triage', 'write', 'maintain', 'admin'];

/** The booleans GitHub returns, strongest first, so the first hit is the role. */
export const PERMISSION_FLAGS = [
  ['admin', 'admin'],
  ['maintain', 'maintain'],
  ['push', 'write'],
  ['triage', 'triage'],
  ['pull', 'read'],
];

/** The legacy permission string rounds maintain to write and triage to read. */
export const LEGACY_ROUNDING = {
  admin: 'admin', write: 'write', read: 'read', none: 'none',
};

/** Minimum role for the actions people actually get refused on. */
export const ACTION_MINIMUM = {
  'read-code': 'read',
  clone: 'read',
  'open-issue': 'read',
  comment: 'read',
  'label-issue': 'triage',
  'close-issue': 'triage',
  'assign-issue': 'triage',
  'request-review': 'triage',
  'push-branch': 'write',
  'merge-pull-request': 'write',
  'create-release': 'write',
  'dismiss-review': 'write',
  'manage-repository-settings': 'maintain',
  'manage-webhooks': 'admin',
  'add-collaborator': 'admin',
  'change-visibility': 'admin',
};

/** Longest prefixes first. */
export const TOKEN_PREFIXES = [
  ['github_pat_', 'fine-grained PAT'],
  ['ghp_', 'classic PAT'],
  ['gho_', 'OAuth user token'],
  ['ghu_', 'App user-to-server token'],
  ['ghs_', 'App installation token'],
  ['ghr_', 'App refresh token'],
];

/** The widest a classic token gets on repositories. */
export const WIDEST_CLASSIC_REPO_SCOPE = 'repo';

/** Requests this run will spend against the core quota. Pure. */
export function readCost(withUser = false) {
  return withUser ? 3 : 2;
}

/** Name the credential from its prefix. Pure. */
export function tokenKind(token) {
  const value = String(token ?? '').trim();
  for (const [prefix, name] of TOKEN_PREFIXES) {
    if (value.startsWith(prefix)) return name;
  }
  return 'unknown';
}

/** Read x-oauth-scopes into a list, keeping absent and empty apart. Pure. */
export function scopeList(headerValue) {
  if (headerValue === null || headerValue === undefined) return null;
  return String(headerValue).split(',').map((s) => s.trim()).filter(Boolean);
}

/** Position in the hierarchy, or -1 for something unrecognised. Pure. */
export function roleRank(role) {
  return ROLES.indexOf(String(role ?? 'none').trim().toLowerCase());
}

/** The role a permissions object describes. Pure. */
export function roleFromPermissions(permissions) {
  if (!permissions || typeof permissions !== 'object'
      || Object.keys(permissions).length === 0) {
    return 'unreported';
  }
  for (const [flag, role] of PERMISSION_FLAGS) {
    if (permissions[flag] === true) return role;
  }
  return 'none';
}

/** Resolve the collaborator permission endpoint. Pure. [role, exact, note]. */
export function roleFromCollaborator(payload) {
  if (!payload || typeof payload !== 'object') {
    return ['unreported', false, 'no collaborator permission payload was read.'];
  }
  const name = String(payload.role_name ?? '').trim().toLowerCase();
  const legacy = String(payload.permission ?? '').trim().toLowerCase();
  if (name && roleRank(name) >= 0) {
    return [name, true, 'role_name reported the exact role.'];
  }
  if (name) {
    return [`custom:${name}`, false, `role_name is '${name}', a custom `
      + 'organization role. Its abilities are defined by the organization and '
      + 'are not published through this API, so nothing here prices it.'];
  }
  if (Object.prototype.hasOwnProperty.call(LEGACY_ROUNDING, legacy)) {
    return [LEGACY_ROUNDING[legacy], false, 'only the legacy permission field '
      + 'was present. It rounds maintain to write and triage to read, so a '
      + 'maintainer and a triager are both misreported by it.'];
  }
  return ['unreported', false, 'neither role_name nor permission was present.'];
}

/** Does this role reach the documented minimum for this action. Pure. */
export function can(role, action) {
  const needed = ACTION_MINIMUM[String(action ?? '').trim().toLowerCase()];
  if (needed === undefined) return null;
  const held = roleRank(role);
  if (held < 0) return null;
  return held >= roleRank(needed);
}

/** How many roles short this account is. 0 is sufficient, null unanswerable. */
export function deficit(role, action) {
  const needed = ACTION_MINIMUM[String(action ?? '').trim().toLowerCase()];
  const held = roleRank(role);
  if (needed === undefined || held < 0) return null;
  return Math.max(0, roleRank(needed) - held);
}

/** Every documented action this role cannot perform. Pure. */
export function blockedActions(role) {
  const held = roleRank(role);
  if (held < 0) return [];
  return Object.keys(ACTION_MINIMUM)
    .filter((a) => held < roleRank(ACTION_MINIMUM[a]))
    .sort();
}

/** Is widening the credential capable of changing the answer. Pure. */
export function scopesAreTheCeiling(role, scopes, kind, action) {
  const short = deficit(role, action);
  if (short === null || short === 0) {
    return ['not-the-question', 'the role is sufficient for this action, so the '
      + 'credential is the next thing to look at rather than the first.'];
  }
  if (scopes === null || scopes === undefined) {
    return ['no-scopes-to-widen', `this credential is a ${kind} and carries no `
      + 'OAuth scopes at all, so there is nothing to widen. Its per-resource '
      + 'permissions are a separate gate and neither gate raises a repository role.'];
  }
  if (scopes.includes(WIDEST_CLASSIC_REPO_SCOPE)) {
    return ['scopes-are-not-the-ceiling', `the token carries `
      + `'${WIDEST_CLASSIC_REPO_SCOPE}', which is as wide as a classic token `
      + 'gets on repositories. Reminting it wider cannot change this answer.'];
  }
  return ['two-gates-open', `the token holds ${scopes.join(', ') || 'no scopes at all'} `
    + `and not '${WIDEST_CLASSIC_REPO_SCOPE}', so the scope is worth fixing too. `
    + 'Fixing it alone will not help: the role is short as well, and both gates '
    + 'have to open.'];
}

/** Classify one account's role against one action. Pure. [state, detail]. */
export function verdict(role, action) {
  if (String(role).startsWith('custom:')) {
    return ['custom-role', 'the role is a custom organization role, which this '
      + 'script names and does not price. Ask an organization owner what it '
      + 'grants, or compare against the base role it was built from.'];
  }
  if (role === 'unreported') {
    return ['role-unreported', 'no permissions object came back. An '
      + 'unauthenticated read never carries one, so authenticate before reading '
      + 'anything into this.'];
  }
  if (role === 'none') {
    return ['no-access', 'the account has no role on this repository at all. '
      + 'Reads of a private repository will 404 rather than 403, which is a '
      + 'different symptom with the same cause.'];
  }
  const short = deficit(role, action);
  if (short === null) {
    return ['action-unknown', 'no documented minimum role is held here for that '
      + 'action, so the role is reported and the comparison is left to you.'];
  }
  if (short === 0) {
    return ['role-sufficient', `this account holds '${role}' and ${action} needs `
      + `'${ACTION_MINIMUM[action]}', so the role is not what refused the call.`];
  }
  return ['role-insufficient', `this account holds '${role}' and ${action} needs `
    + `'${ACTION_MINIMUM[action]}', which is ${short} role(s) higher.`];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state, role, action, subject = 'this account') {
  if (state === 'role-insufficient') {
    return `have somebody with admin raise ${subject} to '${ACTION_MINIMUM[action]}' `
      + 'on this repository, or add it to a team that has it. The permissions '
      + 'object reports the effective role and never its source, so the grant may '
      + "need making in a team or in the org's base permission.";
  }
  if (state === 'no-access') {
    return `grant ${subject} a role on the repository. Until then the repository `
      + 'is invisible rather than forbidden if it is private.';
  }
  if (state === 'role-sufficient') {
    return 'nothing on the role. Read the refusal\'s headers next: a classic '
      + 'token names what it accepts in x-accepted-oauth-scopes and a '
      + 'fine-grained one names nothing at all.';
  }
  if (state === 'custom-role') {
    return 'ask an organization owner which base role this custom role was built '
      + 'from, then compare that against the action.';
  }
  if (state === 'role-unreported') {
    return 'authenticate the read. The permissions object only arrives on an '
      + 'authenticated request.';
  }
  return 'name an action to turn the role into a verdict. The role itself is '
    + 'already reported above.';
}

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const repoName = (process.env.GITHUB_REPO || "dummy-github-repo");
  if (!token || !repoName) {
    console.error('set GITHUB_TOKEN (read-only is enough) and GITHUB_REPO');
    process.exitCode = 2;
    return;
  }
  const action = (process.env.GITHUB_ACTIO || "dummy-github-actio")N || 'merge-pull-request';
  const user = (process.env.GITHUB_USE || "dummy-github-use")R || '';

  console.log(`read cost: ${readCost(Boolean(user))} request(s) against the core hourly quota`);

  const me = await fetch(`${API}/user`, { headers: headers(token) });
  const kind = tokenKind(token);
  const scopes = scopeList(me.headers.get('x-oauth-scopes'));
  console.log(`token: ${kind}, scopes=${scopes === null ? 'none' : (scopes.join(', ') || 'empty')}`);

  const res = await fetch(`${API}/repos/${repoName}`, { headers: headers(token) });
  if (res.status !== 200) {
    console.error(`${repoName}: HTTP ${res.status} reading the repository`);
    process.exitCode = 2;
    return;
  }
  const repo = await res.json();
  const permissions = repo.permissions || {};
  let role = roleFromPermissions(permissions);
  let subject = 'this account';

  if (user) {
    const collab = await fetch(
      `${API}/repos/${repoName}/collaborators/${user}/permission`,
      { headers: headers(token) },
    );
    if (collab.status === 200) {
      const [resolved, , note] = roleFromCollaborator(await collab.json());
      role = resolved;
      subject = user;
      console.log(`role source: ${note}`);
    }
  }

  console.log(`${repoName}: permissions=${JSON.stringify(permissions)}`);
  console.log(`role: ${role}`);
  const [state, detail] = verdict(role, action);
  console.log(`${state}: ${detail}`);
  const [ceilingState, ceilingDetail] = scopesAreTheCeiling(role, scopes, kind, action);
  console.log(`${ceilingState}: ${ceilingDetail}`);
  console.log(`repair: ${repair(state, role, action, subject)}`);

  console.log(JSON.stringify({
    repository: repoName,
    subject,
    token_kind: kind,
    scopes,
    permissions,
    role,
    action,
    minimum_role: ACTION_MINIMUM[action] ?? null,
    roles_short: deficit(role, action),
    state,
    detail,
    credential_state: ceilingState,
    blocked_actions: blockedActions(role),
    repair: repair(state, role, action, subject),
  }, null, 2));
  process.exitCode = ['role-insufficient', 'no-access'].includes(state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
