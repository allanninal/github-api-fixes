/**
 * Say whether an endpoint refused you because the feature is switched off.
 *
 * Read only. GET requests and nothing else. The repository object carries every
 * feature flag, so nothing here is established by attempting anything.
 *
 * Some endpoints are gated on a repository feature as well as on a permission.
 * When the feature is off they refuse everybody, and one off switch produces
 * three different status codes depending on the endpoint family: 403 for code
 * scanning, 404 for secret scanning, 410 for issues.
 *
 * Environment:
 *   GITHUB_TOKEN        a token with read access to the repository
 *   GITHUB_REPO         owner/name
 *   GITHUB_ENDPOINT     the endpoint that refused you
 *   GITHUB_STATUS       the status code you recorded from it
 *   GITHUB_ACCEPTED     x-accepted-github-permissions, if the response had one
 */
const API = 'https://api.github.com';
const UA = 'github-feature-flags/1.0';

/** Read out of security_and_analysis, a map of {name: {status}}. */
export const SECURITY_FEATURES = [
  'advanced_security',
  'secret_scanning',
  'secret_scanning_push_protection',
  'secret_scanning_non_provider_patterns',
  'dependabot_security_updates',
];

/** Plain booleans on the repository object, visible to any reader. */
export const TOGGLES = ['has_issues', 'has_wiki', 'has_projects',
  'has_discussions', 'has_pages', 'has_downloads'];

/** Flag, where it is read, status when disabled, and how exact the mapping is. */
export const ENDPOINT_FEATURES = {
  '/code-scanning/alerts': ['advanced_security', 'security', 403, 'exact'],
  '/code-scanning/analyses': ['advanced_security', 'security', 403, 'exact'],
  '/secret-scanning/alerts': ['secret_scanning', 'security', 404, 'exact'],
  '/dependabot/alerts': ['dependabot_security_updates', 'security', 403, 'proxy'],
  '/issues': ['has_issues', 'toggle', 410, 'exact'],
  '/issues/comments': ['has_issues', 'toggle', 410, 'exact'],
  '/milestones': ['has_issues', 'toggle', 410, 'exact'],
};

/** Why the proxy rows are not presented as certainties. */
export const PROXY_NOTE = 'this flag is the closest one the repository object '
  + 'publishes for that endpoint rather than a switch for it exactly, so a '
  + 'disabled reading here is strong evidence and not proof.';

/** Advanced Security on a private repository depends on the plan too. */
export const PLAN_DEPENDENT = ['advanced_security', 'secret_scanning',
  'secret_scanning_push_protection'];

/** Requests this run will spend against the core quota. Pure. */
export function readCost(probes = 0) {
  return 1 + Math.max(0, Math.trunc(Number(probes) || 0));
}

/** Reduce a logged URL to a key in the table. Pure. */
export function normaliseEndpoint(path) {
  let text = String(path ?? '').trim();
  for (const prefix of ['https://api.github.com', 'http://api.github.com']) {
    if (text.startsWith(prefix)) text = text.slice(prefix.length);
  }
  [text] = text.split('?');
  text = text.replace(/\/+$/, '');
  if (!text) return '';
  if (!text.startsWith('/')) text = `/${text}`;
  if (text.startsWith('/repos/')) {
    const parts = text.split('/');
    text = parts.length > 4 ? `/${parts.slice(4).join('/')}` : '/';
  }
  return text;
}

/** The table row for an endpoint, or null. Pure. */
export function featureFor(path) {
  const key = normaliseEndpoint(path);
  const row = ENDPOINT_FEATURES[key];
  if (!row) return null;
  const [feature, source, status, confidence] = row;
  return {
    endpoint: key, feature, source, status_when_disabled: status, confidence,
  };
}

/** The security_and_analysis map, or null when it was not returned. Pure. */
export function securityBlock(repo) {
  const block = (repo || {}).security_and_analysis;
  return block && typeof block === 'object' ? block : null;
}

/** enabled, disabled or unreported for one feature. Pure. */
export function flagState(repo, feature, source) {
  if (source === 'toggle') {
    const value = (repo || {})[feature];
    if (value === true) return 'enabled';
    if (value === false) return 'disabled';
    return 'unreported';
  }
  const block = securityBlock(repo);
  if (!block) return 'unreported';
  const entry = block[feature];
  if (!entry || typeof entry !== 'object') return 'unreported';
  const status = String(entry.status ?? '').trim().toLowerCase();
  return status === 'enabled' || status === 'disabled' ? status : 'unreported';
}

/** Every endpoint with the state of the flag gating it. Pure. */
export function matrix(repo) {
  return Object.keys(ENDPOINT_FEATURES).sort().map((key) => {
    const row = featureFor(key);
    row.state = flagState(repo, row.feature, row.source);
    row.will_serve = { enabled: true, disabled: false }[row.state] ?? null;
    return row;
  });
}

/** Is this a repair a repository admin might not be able to make. Pure. */
export function planMayBeTheConstraint(repo, feature) {
  if (!PLAN_DEPENDENT.includes(feature)) return false;
  const visibility = String((repo || {}).visibility ?? '').trim().toLowerCase();
  return Boolean((repo || {}).private) || ['private', 'internal'].includes(visibility);
}

/** Does the recorded status match what a disabled feature produces. Pure. */
export function statusMatches(row, observed) {
  if (observed === null || observed === undefined || observed === '') return null;
  const n = Number(observed);
  if (!Number.isFinite(n)) return null;
  return n === Number(row.status_when_disabled);
}

/** Attribute one refusal to the switch or to the grant. Pure. */
export function classify(repo, row, observedStatus = null, acceptedPermissions = null) {
  if (!row) {
    return ['endpoint-unknown', 'that endpoint is not one of the feature-gated '
      + 'ones in this table, so a refusal from it is not this note. Read the '
      + 'whole flag matrix above and check the permission headers instead.'];
  }
  const stateOfFlag = row.state ?? flagState(repo, row.feature, row.source);
  const named = String(acceptedPermissions ?? '').trim();
  const match = statusMatches(row, observedStatus);

  if (stateOfFlag === 'unreported') {
    return ['feature-unreported', `${row.feature} could not be read. The `
      + 'security_and_analysis block is only returned to a caller with admin on '
      + 'the repository, so this says something about your own role rather than '
      + 'about the feature.'];
  }
  if (stateOfFlag === 'disabled') {
    if (match === false) {
      return ['status-mismatch', `${row.feature} is disabled, but a disabled `
        + `feature answers ${row.status_when_disabled} on this endpoint and you `
        + `recorded ${observedStatus}. Fix the feature and expect the other `
        + 'failure to survive it.'];
    }
    return ['feature-disabled', `${row.feature} is disabled on this repository, `
      + `and ${row.status_when_disabled} is what a disabled feature produces `
      + 'here. No permission opens it.'];
  }
  if (named) {
    return ['permission-named', `${row.feature} is enabled and the response `
      + `named '${named}' in x-accepted-github-permissions, so this is a grant `
      + 'that is missing rather than a feature that is off.'];
  }
  return ['feature-enabled', `${row.feature} is enabled, so the feature is not `
    + 'what refused you. Look at the credential next: a fine-grained token names '
    + 'no permission on its own refusal, and an App names one in a header.'];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state, row, repo = {}) {
  const feature = (row || {}).feature || 'the feature';
  if (state === 'feature-disabled') {
    let text = `enable ${feature} on this repository in its security settings, `
      + 'or at organization level for every repository, then grant the caller '
      + 'the matching read permission. Both, in that order.';
    if (planMayBeTheConstraint(repo, feature)) {
      text += ' This is a private or internal repository, so availability '
        + 'depends on the plan as well as on the checkbox, and that part is not '
        + 'a repository setting.';
    }
    if ((row || {}).confidence === 'proxy') text += ` Note that ${PROXY_NOTE}`;
    return text;
  }
  if (state === 'feature-unreported') {
    return 'read the repository with an account that has admin on it, or ask an '
      + 'admin what the setting says. Do not record this as disabled: an absent '
      + 'block is a limit on your reading.';
  }
  if (state === 'permission-named') {
    return 'grant the named permission. The feature is on, so this is the '
      + 'ordinary permissions path and not this note.';
  }
  if (state === 'status-mismatch') {
    return 'enable the feature anyway, then diagnose the recorded status '
      + 'separately. Two causes were in play and only one of them is addressed here.';
  }
  if (state === 'feature-enabled') {
    return 'look at the credential. Nothing about the repository features '
      + 'explains this refusal.';
  }
  return 'name the endpoint that refused you so the flag can be mapped to it.';
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
  console.log(`read cost: ${readCost(0)} request(s) against the core hourly quota`);

  const res = await fetch(`${API}/repos/${repoName}`, { headers: headers(token) });
  if (res.status !== 200) {
    console.error(`${repoName}: HTTP ${res.status} reading the repository`);
    process.exitCode = 2;
    return;
  }
  const repo = await res.json();

  console.log(`${repoName}: private=${repo.private}`);
  if (!securityBlock(repo)) {
    console.log('security_and_analysis: not returned. That block is only sent to '
      + 'a caller with admin on the repository.');
  } else {
    console.log(`security_and_analysis: ${SECURITY_FEATURES
      .map((f) => `${f}=${flagState(repo, f, 'security')}`).join(' ')}`);
  }
  console.log(`toggles: ${TOGGLES.map((t) => `${t}=${repo[t]}`).join(' ')}`);

  const target = (process.env.GITHUB_ENDPOINT || "https://example.com")
    ? featureFor((process.env.GITHUB_ENDPOINT || "https://example.com")) : null;
  if (target) {
    target.state = flagState(repo, target.feature, target.source);
    console.log(`${target.endpoint} -> ${target.feature} (${target.confidence}), `
      + `${target.status_when_disabled} when disabled`);
  }
  const [state, detail] = classify(repo, target, (process.env.GITHUB_STATUS || "dummy-github-status"),
    (process.env.GITHUB_ACCEPTED || "dummy-github-accepted"));
  console.log(`${state}: ${detail}`);
  console.log(`repair: ${repair(state, target, repo)}`);

  console.log(JSON.stringify({
    repository: repoName,
    private: repo.private,
    visibility: repo.visibility,
    security_block_returned: securityBlock(repo) !== null,
    matrix: matrix(repo),
    endpoint: target,
    state,
    detail,
    repair: repair(state, target, repo),
  }, null, 2));
  process.exitCode = ['feature-disabled', 'status-mismatch'].includes(state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
