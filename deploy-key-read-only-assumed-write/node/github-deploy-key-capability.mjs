/**
 * Check whether a repository's deploy keys can do what its automation needs.
 *
 * Read only. One GET per repository, nothing is written, and the keys are never
 * exercised: no push is attempted to find out whether a key can push. The
 * capability is declared on the key object as read_only.
 *
 * A deploy key's read_only flag is chosen at creation and cannot be edited
 * afterwards, so a read-only key added for a CI job that reads works perfectly
 * until somebody adds a push step. The refusal then arrives from Git over SSH
 * rather than from the API, and no scope or token change moves it.
 *
 * The public key material is dropped before anything is printed.
 *
 * Environment:
 *   GITHUB_TOKEN       read access plus repository admin, for the keys endpoint
 *   GITHUB_REPOS       comma-separated owner/name values
 *   GITHUB_NEEDS_WRITE set to 1 when the automation pushes over SSH
 *   GITHUB_GIT_ERROR   the line your build log recorded
 */
const API = 'https://api.github.com';
const UA = 'github-deploy-key-capability/1.0';

/** The only fields that leave this script. The key material is not among them. */
export const SAFE_FIELDS = ['id', 'title', 'read_only', 'created_at', 'verified',
  'added_by'];

/** A deploy key older than this is worth a look during the same read. */
export const DEFAULT_MAX_AGE_DAYS = 365;

/** One deploy key reduced to metadata. Pure. Never carries key material. */
export function redact(key) {
  if (!key || typeof key !== 'object') return {};
  const out = {};
  for (const field of SAFE_FIELDS) {
    if (field in key) out[field] = key[field];
  }
  return out;
}

/** The whole listing, reduced. Pure. */
export function redactAll(keys) {
  return (keys || []).filter((k) => k && typeof k === 'object').map(redact);
}

/** What this key is allowed to do, as declared. Pure. */
export function capability(key) {
  if (!key || typeof key !== 'object' || !('read_only' in key)) return 'unknown';
  return key.read_only ? 'read-only' : 'read-write';
}

/** The ids of keys that can push. Pure. */
export function writableKeys(keys) {
  return (keys || []).filter((k) => k && typeof k === 'object'
    && capability(k) === 'read-write').map((k) => k.id);
}

/** Classify one repository's deploy keys. Pure. [state, detail]. */
export function verdict(status, keys, needsWrite) {
  const code = Number(status);

  if (code !== 200) {
    if (code === 403 || code === 404) {
      return ['keys-unreadable', 'the deploy keys endpoint needs repository '
        + 'admin and this token does not have it. That is not the same as the '
        + 'repository having no keys.'];
    }
    return ['keys-unreadable', 'the deploy keys could not be listed, so nothing '
      + 'here is a finding about the keys.'];
  }

  const rows = (keys || []).filter((k) => k && typeof k === 'object');
  const writable = writableKeys(rows);

  if (!rows.length) {
    if (needsWrite) {
      return ['no-deploy-keys', 'this repository has no deploy keys at all, so '
        + 'a push over SSH is authenticating with something else or not at all.'];
    }
    return ['no-deploy-keys', 'this repository has no deploy keys, which is fine '
      + 'if nothing clones it over SSH.'];
  }

  if (needsWrite && !writable.length) {
    return ['write-needed-none-capable', "this repository's automation pushes "
      + `and all ${rows.length} deploy key(s) on it are read-only, which is the `
      + 'whole failure.'];
  }
  if (needsWrite) {
    return ['write-capable-key-present', `${writable.length} of ${rows.length} `
      + 'deploy key(s) can push, so a read-only key is not what refused this write.'];
  }
  if (writable.length) {
    return ['write-capable-but-unused', `${writable.length} deploy key(s) can `
      + 'push on a repository whose automation only reads. That is a standing '
      + 'grant rather than a failure.'];
  }
  return ['read-only-and-correct', 'every deploy key is read-only and nothing '
    + 'here needs to push, which is the recommended arrangement.'];
}

/** Work out which credential refused a push, from the message. Pure. */
export function attributeGitError(text) {
  const message = String(text ?? '').toLowerCase();
  if (!message.trim()) return ['no-message', 'nothing was supplied to attribute.'];
  if (message.includes('marked as read only') || message.includes('marked as read-only')) {
    return ['deploy-key-read-only', 'the message names the key itself, so the '
      + "refusal is the key's declared capability and not a scope, a token or SSH."];
  }
  if (message.includes('protected branch') || message.includes('gh006')) {
    return ['refused-by-branch-protection', 'the credential was accepted and the '
      + 'branch refused the update. That is a rule on the ref rather than a '
      + 'capability problem.'];
  }
  if (message.includes('archived')) {
    return ['repository-archived', 'the repository is archived and read-only, so '
      + 'no credential of any kind can write to it.'];
  }
  if (message.includes('permission denied (publickey)')) {
    return ['key-not-accepted', 'the key was not accepted at all, so it is not on '
      + 'this repository or the agent presented a different one. This is '
      + 'authentication, not capability.'];
  }
  if (message.includes('write access to repository not granted')) {
    return ['write-not-granted', 'the write was refused without naming the key. '
      + 'Over SSH that is a read-only deploy key; over HTTPS it is the token or '
      + 'the installation. The keys listing settles it.'];
  }
  return ['unattributed', 'the message does not name a known refusal. List the '
    + 'keys anyway and check which credential the remote URL implies.'];
}

/** How old a key is, in whole days. Pure. Null when unreadable. */
export function ageDays(createdAt, now = Date.now()) {
  if (!createdAt) return null;
  const when = Date.parse(String(createdAt));
  if (!Number.isFinite(when)) return null;
  return Math.max(0, Math.floor((now - when) / 86400000));
}

/** Keys older than the rotation policy. Pure. Metadata only. */
export function staleKeys(keys, maxAgeDays = DEFAULT_MAX_AGE_DAYS, now = Date.now()) {
  const out = [];
  for (const key of keys || []) {
    if (!key || typeof key !== 'object') continue;
    const age = ageDays(key.created_at, now);
    if (age !== null && age >= maxAgeDays) out.push({ ...redact(key), age_days: age });
  }
  return out;
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (['write-needed-none-capable', 'deploy-key-read-only'].includes(state)) {
    return 'create a replacement deploy key with write access and delete the old '
      + 'one, or move the job to a GitHub App installation token with '
      + 'contents: write, which is scoped, expiring and auditable. read_only '
      + 'cannot be edited on an existing key.';
  }
  if (state === 'write-capable-key-present') {
    return 'look elsewhere for this refusal: a key that can push exists, so check '
      + 'the branch rules and the repository state.';
  }
  if (state === 'write-capable-but-unused') {
    return 'delete the write-capable key if nothing pushes with it. A standing '
      + 'write grant on a repository that only gets read is the kind of thing '
      + 'nobody revisits.';
  }
  if (state === 'read-only-and-correct') {
    return 'nothing. Read-only is the recommended default and this repository '
      + 'matches what its automation does.';
  }
  if (state === 'no-deploy-keys') {
    return 'check which credential your clone actually uses. With no deploy keys, '
      + 'an SSH remote is authenticating as a user rather than as the repository.';
  }
  if (state === 'keys-unreadable') {
    return 'run this with a token that has repository admin, or an App with '
      + 'administration: read. Do not record the keys as absent.';
  }
  if (state === 'refused-by-branch-protection') {
    return 'read the branch rules rather than the credential. The push was '
      + 'authorised and the ref refused it.';
  }
  if (state === 'repository-archived') {
    return 'skip the repository. An archived repository is read-only for every '
      + 'credential.';
  }
  if (state === 'key-not-accepted') {
    return 'fix authentication first: confirm the public key is on this '
      + 'repository and that the agent is presenting the matching private key.';
  }
  if (state === 'write-not-granted') {
    return 'check the remote URL. An SSH remote points at the deploy keys, an '
      + 'HTTPS one points at the token or the installation.';
  }
  return 'list the deploy keys and read read_only before investigating SSH or scopes.';
}

/** Requests this run will spend against the core quota. Pure. */
export function readCost(repos) {
  return (repos || []).length;
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
  const names = ((process.env.GITHUB_REPO || "dummy-github-repo")S || '').split(',')
    .map((n) => n.trim()).filter(Boolean);
  if (!token || !names.length) {
    console.error('set GITHUB_TOKEN and GITHUB_REPOS');
    process.exitCode = 2;
    return;
  }
  const needsWrite = (process.env.GITHUB_NEEDS_WRITE || "dummy-github-needs-write") === '1';

  console.log('read cost: 1 request(s) per repository against the core hourly quota');
  console.log(`read cost: ${readCost(names)} request(s) in total`);

  let attributed = null;
  const gitError = (process.env.GITHUB_GIT_ERRO || "dummy-github-git-erro")R || '';
  if (gitError) {
    const [state, detail] = attributeGitError(gitError);
    console.log(`git error -> ${state}: ${detail}`);
    console.log(`repair: ${repair(state)}`);
    attributed = { state, detail, repair: repair(state) };
  }

  const findings = [];
  for (const name of names) {
    const res = await fetch(`${API}/repos/${name}/keys?per_page=100`,
      { headers: headers(token) });
    let body = null;
    try { body = await res.json(); } catch { body = null; }
    // Reduced here, once. Nothing below this line has the key material.
    const rows = redactAll(Array.isArray(body) ? body : []);
    const [state, detail] = verdict(res.status, rows, needsWrite);
    const stale = staleKeys(rows);

    console.log(`${name}: ${rows.length} deploy key(s), `
      + `${writableKeys(rows).length} of them write-capable`);
    for (const row of rows) {
      const age = ageDays(row.created_at);
      console.log(`  key ${row.id} "${row.title}" ${capability(row)} created `
        + `${String(row.created_at || '').slice(0, 10)} by ${row.added_by || 'unknown'}`
        + (age === null ? '' : `, ${age} day(s) old`));
    }
    console.log(`${state}: ${detail}`);
    console.log(`repair: ${repair(state)}`);
    if (stale.length) {
      console.log(`rotation: ${stale.length} key(s) older than ${DEFAULT_MAX_AGE_DAYS} day(s)`);
    }

    findings.push({
      repository: name,
      keys_status: res.status,
      keys: rows,
      write_capable_ids: writableKeys(rows),
      stale_keys: stale,
      state,
      detail,
      repair: repair(state),
    });
  }

  console.log(JSON.stringify({
    requests_spent: readCost(names),
    git_error: attributed,
    findings,
  }, null, 2));
  const bad = ['write-needed-none-capable', 'write-capable-but-unused'];
  process.exitCode = findings.some((f) => bad.includes(f.state)) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
