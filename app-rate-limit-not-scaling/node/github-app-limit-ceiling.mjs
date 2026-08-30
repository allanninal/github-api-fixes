/**
 * Say whether a GitHub App installation has the rate-limit ceiling it earns.
 *
 * Read only. Two GETs: the rate-limit endpoint, which does not consume quota,
 * and a one-item page of the installation's repositories. Nothing is minted,
 * widened or changed.
 *
 * Environment:
 *   GITHUB_INSTALLATION_TOKEN  an installation access token
 *   GITHUB_ORG                 optional, the account behind the installation
 */
const API = 'https://api.github.com';
const UA = 'github-app-limit-ceiling/1.0';

/** The documented shape of the ceiling. */
export const BASELINE = 5000;
export const PER_UNIT = 50;
export const SCALING_FLOOR = 20;
export const FREE_CEILING = 12500;
export const ENTERPRISE_CEILING = 15000;
export const ANONYMOUS = 60;

/**
 * The hourly ceiling an installation of this size earns. Pure.
 * users may be null: the user term only adds, so an unknown count makes the
 * answer a lower bound rather than a guess.
 */
export function entitled(repositories, users = null, enterprise = false) {
  if (enterprise) return ENTERPRISE_CEILING;
  const repos = Number.isFinite(Number(repositories)) ? Number(repositories) : 0;
  const people = Number.isFinite(Number(users)) ? Number(users) : 0;
  const extra = Math.max(0, repos - SCALING_FLOOR) + Math.max(0, people - SCALING_FLOOR);
  return Math.min(FREE_CEILING, BASELINE + PER_UNIT * extra);
}

/** Whether the entitlement was computed without the user term. Pure. */
export function isLowerBound(users) {
  return users === null || users === undefined;
}

/** Name the ceiling a credential was actually given. Pure. */
export function classifyCeiling(limit) {
  const value = Number(limit);
  if (!Number.isFinite(value)) return 'unknown';
  if (value === ANONYMOUS) return 'unauthenticated';
  if (value === ENTERPRISE_CEILING) return 'enterprise';
  if (value === FREE_CEILING) return 'at-cap';
  if (value === BASELINE) return 'baseline';
  if (value > BASELINE && value < FREE_CEILING) return 'scaled';
  return 'unknown';
}

/** The repository_selection on an installation view, normalised. Pure. */
export function selectionOf(view) {
  if (!view || typeof view !== 'object') return 'unknown';
  const raw = String(view.repository_selection ?? '').trim().toLowerCase();
  return ['all', 'selected'].includes(raw) ? raw : 'unknown';
}

/** How many repositories the installation covers, or null. Pure. */
export function reachable(view) {
  if (!view || typeof view !== 'object') return null;
  const raw = view.total_count;
  if (raw === null || raw === undefined) return null;
  const n = Number(raw);
  return Number.isFinite(n) ? Math.trunc(n) : null;
}

/** Requests an hour the installation is not getting, or 0. Pure. */
export function shortfall(limit, entitlement) {
  const num = (v) => (v === null || v === undefined || v === '' ? NaN : Number(v));
  const have = num(limit);
  const earned = num(entitlement);
  if (!Number.isFinite(have) || !Number.isFinite(earned)) return 0;
  return Math.max(0, earned - have);
}

/** How many repositories a loop of this cost fits under the ceiling. Pure. */
export function sustainableRepos(limit, callsPerRepo) {
  const ceiling = Number(limit);
  const cost = Number(callsPerRepo);
  if (!Number.isFinite(ceiling) || !Number.isFinite(cost) || cost <= 0) return null;
  return Math.floor(ceiling / cost);
}

/** Turn the two reads into a finding. Pure. */
export function verdict(limit, selection, covered, accountRepos = null,
                        users = null, enterprise = false, installationSeen = true) {
  const klass = classifyCeiling(limit);
  if (klass === 'unauthenticated') {
    return ['unauthenticated',
      'the ceiling is 60/hour, which is the anonymous ceiling. This credential '
      + 'is not reaching GitHub as an installation at all.'];
  }
  if (!installationSeen) {
    return ['not-an-installation',
      `the ceiling is ${limit}/hour and the installation endpoint did not `
      + 'answer, so this is a user or Actions credential rather than an '
      + 'installation token. Installation scaling does not apply to it.'];
  }
  if (klass === 'enterprise') {
    return ['enterprise',
      'the ceiling is 15000/hour, the flat Enterprise Cloud ceiling. Widening '
      + 'the installation cannot raise it further.'];
  }
  const size = accountRepos === null || accountRepos === undefined ? covered : accountRepos;
  const earned = entitled(size, users, enterprise);
  if (klass === 'at-cap') {
    return ['at-cap',
      'the ceiling is 12500/hour, the maximum outside Enterprise Cloud. There '
      + 'is no more to earn: spend fewer requests.'];
  }
  const gap = shortfall(limit, earned);
  if (gap && selection === 'selected') {
    return ['narrow-installation',
      `the ceiling is ${limit}/hour, and an installation covering ${size} `
      + `repositories would be entitled to at least ${earned}/hour. The `
      + 'selection is what is capping it, not the account.'];
  }
  if (gap) {
    return ['below-entitlement',
      `the ceiling is ${limit}/hour against an entitlement of at least `
      + `${earned}/hour for this size. The installation is narrower than the `
      + 'size used for the comparison.'];
  }
  if (klass === 'baseline') {
    return ['baseline',
      `the ceiling is 5000/hour and the installation covers ${covered} `
      + 'repositories, which is too few to earn any scaling. This ceiling is '
      + 'real: the repair is on the usage side.'];
  }
  return ['scaled', `the ceiling is ${limit}/hour, which matches an installation this size.`];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (state === 'narrow-installation') {
    return 'widen the installation to all repositories if the App legitimately '
      + 'needs org-wide reach, which raises the ceiling as a side effect. If it '
      + 'does not, keep the narrow selection and cut request volume instead: '
      + 'conditional requests, a bigger per_page, one GraphQL query for a '
      + 'fan-out of REST calls.';
  }
  if (['at-cap', 'enterprise', 'baseline'].includes(state)) {
    return 'nothing on the installation. This ceiling is the one you get, so '
      + 'the only lever left is spending fewer requests per unit of work.';
  }
  if (state === 'unauthenticated') {
    return 'send the installation access token in the Authorization header. '
      + 'Nothing about scaling matters while the requests are arriving anonymously.';
  }
  if (state === 'not-an-installation') {
    return 'point the check at an installation access token. A user token gets '
      + 'a flat 5000 and never scales, so comparing it against an installation '
      + 'entitlement is meaningless.';
  }
  if (state === 'below-entitlement') {
    return 'check repository_selection and the account behind the installation '
      + 'before widening anything: the numbers disagree for a reason this '
      + 'script could not see.';
  }
  return 'nothing.';
}

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
}

async function get(token, path) {
  const res = await fetch(API + path, { headers: headers(token) });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return { status: res.status, body };
}

async function main() {
  const token = (process.env.GITHUB_INSTALLATION_TOKEN || "dummy-github-installation-token");
  if (!token) {
    console.error('set GITHUB_INSTALLATION_TOKEN to an installation access token');
    process.exitCode = 2;
    return;
  }
  const org = (process.env.GITHUB_OR || "dummy-github-or")G || null;
  const callsPerRepo = Number((process.env.GITHUB_CALLS_PER_REP || "dummy-github-calls-per-rep")O || 10);

  const rate = await get(token, '/rate_limit');
  if (rate.status !== 200 || !rate.body) {
    console.error(`GET /rate_limit returned ${rate.status}`);
    process.exitCode = 2;
    return;
  }
  const resources = rate.body.resources || {};
  const core = (resources.core || {}).limit;
  const graphql = (resources.graphql || {}).limit;
  console.log(`core ceiling: ${core}/hour, graphql ${graphql}/hour`);

  const inst = await get(token, '/installation/repositories?per_page=1');
  const view = inst.status === 200 ? inst.body : null;
  const covered = reachable(view);
  const selection = selectionOf(view);
  if (view) {
    console.log(`installation: repository_selection=${selection}, ${covered} reachable`);
  }

  let orgRepos = null;
  if (org && selection === 'selected') {
    const o = await get(token, `/orgs/${org}`);
    if (o.status === 200 && o.body) {
      orgRepos = Number(o.body.public_repos || 0) + Number(o.body.total_private_repos || 0);
    } else {
      console.log(`GET /orgs/${org} returned ${o.status}; using the installation size`);
    }
  }

  const [state, detail] = verdict(core, selection, covered, orgRepos, null,
    false, view !== null);
  console.log(`${state}: ${detail}`);
  console.log(`repair: ${repair(state)}`);
  const fits = sustainableRepos(core, callsPerRepo);
  if (fits !== null) {
    console.log(`budget: ${core}/hour serves ${fits} repositories at ${callsPerRepo} call(s) each`);
  }
  console.log(JSON.stringify({
    core_limit: core,
    graphql_limit: graphql,
    repository_selection: selection,
    repositories_covered: covered,
    account_repositories: orgRepos,
    entitlement_is_lower_bound: isLowerBound(null),
    state,
    repositories_supported: fits,
  }, null, 2));
  process.exitCode = ['narrow-installation', 'below-entitlement', 'unauthenticated']
    .includes(state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
