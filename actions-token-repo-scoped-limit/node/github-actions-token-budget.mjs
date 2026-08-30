/**
 * Cost a workflow against the request pool it shares with its own repository.
 *
 * Read only. Every request is a GET. GET /rate_limit consumes no quota, and the
 * identity probe is a single GET /user read for its status code alone.
 *
 * The built-in Actions credential has a 1,000 an hour core ceiling and that
 * ceiling belongs to the repository, so every concurrent job and every matrix
 * leg draws from the same pool on the same clock.
 */
const API = 'https://api.github.com';
const UA = 'github-actions-token-budget/1.0';

export const ACTIONS_CEILING = 1000;
export const ANON_CEILING = 60;
export const ENTERPRISE_CEILING = 15000;
export const USER_CEILING = 5000;

/**
 * Name the credential class from the ceilings it was handed. Pure.
 * Returns [klass, confidence, note]; 5000 is reported as ambiguous because it
 * names both a user token and an App installation at the floor.
 */
export function classify(coreLimit, graphqlLimit = null, userStatus = null) {
  const core = Number.parseInt(coreLimit, 10);
  if (!Number.isFinite(core)) {
    return ['unknown', 'none',
      'GET /rate_limit reported no core limit, so there is no ceiling to cost anything against'];
  }
  if (core <= 0) return ['unknown', 'none', `a core limit of ${core} is not a ceiling`];
  if (core <= ANON_CEILING) {
    return ['anonymous', 'high',
      `a core ceiling of ${core} is the anonymous tier, counted per originating ` +
      'IP address. No credential is reaching GitHub'];
  }
  if (core === ACTIONS_CEILING) {
    const seconds = [];
    if (userStatus === 403) seconds.push('GET /user answered 403, which a user token never does');
    if (Number.parseInt(graphqlLimit, 10) === ACTIONS_CEILING) {
      seconds.push('the graphql row is 1000 points as well');
    }
    let note = 'a core ceiling of 1000 an hour is the built-in Actions token, ' +
      'and it belongs to the repository rather than to this job';
    if (seconds.length) note += `. ${seconds.join('; ')}`;
    return ['actions-token', seconds.length ? 'high' : 'likely', note];
  }
  if (core === ENTERPRISE_CEILING) {
    return ['enterprise-user', 'likely', '15000 an hour is a user on GitHub Enterprise Cloud'];
  }
  if (core === USER_CEILING) {
    return ['user-or-app', 'ambiguous',
      '5000 an hour is an authenticated user token or a GitHub App installation ' +
      'still at the floor; the number names two things and settles neither'];
  }
  if (core > USER_CEILING) {
    return ['app-installation', 'likely',
      `${core} an hour is above the 5000 floor, which only a GitHub App ` +
      'installation scaled by repositories and users reaches'];
  }
  return ['unknown', 'none', `a core ceiling of ${core} does not match a documented class`];
}

/** What one workflow run costs against a pool the repository shares. Pure. */
export function plan(jobs, callsPerJob, matrixLegs = 1,
                     ceiling = ACTIONS_CEILING, remaining = null) {
  const whole = (value, floor = 0) => {
    const n = Number.parseInt(value, 10);
    return Number.isFinite(n) ? Math.max(floor, n) : floor;
  };
  const legs = Math.max(1, whole(matrixLegs, 1));
  const count = whole(jobs);
  const calls = whole(callsPerJob);
  const cap = Math.max(1, whole(ceiling, 1));

  const effective = count * legs;
  const total = effective * calls;
  const headroom = remaining === null || remaining === undefined ? cap : whole(remaining);
  const source = remaining === null || remaining === undefined ? 'limit' : 'remaining';
  const served = calls ? Math.min(effective, Math.floor(headroom / calls)) : effective;

  return {
    legs, jobs: effective, calls_per_job: calls, total, headroom, source,
    fits: total <= headroom, jobs_served: served,
    first_starved_job: total <= headroom ? null : served + 1,
    shortfall: Math.max(0, total - headroom),
  };
}

/** Seconds until the shared pool refills; null when unreadable. Pure. */
export function poolResetIn(reset, now) {
  const r = Number.parseInt(reset, 10);
  const n = Number.parseInt(now, 10);
  if (!Number.isFinite(r) || !Number.isFinite(n)) return null;
  return Math.max(0, r - n);
}

/** Turn the class and the costing into a finding. Pure. */
export function verdict(klass, costing) {
  if (klass === 'anonymous') {
    return ['unauthenticated',
      'the ceiling being costed is the anonymous 60 an hour, so this is not a ' +
      'workflow budget problem yet: no credential is arriving at GitHub.'];
  }
  if (costing.total === 0) {
    return ['no-workflow',
      `no workflow was described, so there is nothing to cost against the ${costing.headroom} request pool.`];
  }
  if (klass !== 'actions-token') {
    return ['different-ceiling',
      `the credential in this environment reads as ${klass} with a ceiling of ` +
      `${costing.headroom}, not the 1000 the Actions token gets. The ${costing.total} ` +
      'request(s) this run makes fit here and will not fit there. Run the check ' +
      'from inside the job.'];
  }
  if (!costing.fits) {
    return ['pool-overrun',
      `${costing.jobs} job(s) at ${costing.calls_per_job} call(s) each is ` +
      `${costing.total} request(s) against a pool of ${costing.headroom} that the ` +
      `whole repository shares. Job ${costing.first_starved_job} of ${costing.jobs} ` +
      'is the first to start seeing 403, and any other run in the same hour moves ' +
      'that number down.'];
  }
  if (costing.total * 5 >= costing.headroom * 4) {
    return ['pool-tight',
      `${costing.total} request(s) against ${costing.headroom} is over four fifths ` +
      'of a pool shared with every other job and every other run in this ' +
      'repository within the same hour.'];
  }
  return ['fits', `${costing.total} request(s) against a shared pool of ${costing.headroom}.`];
}

async function get(token, path) {
  const res = await fetch(API + path, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': UA,
    },
  });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return { status: res.status, body };
}

async function main() {
  const envName = (process.env.GITHUB_TOKEN_EN || "dummy-github-token-en")V || 'GITHUB_TOKEN';
  const token = process.env[envName];
  if (!token) {
    console.error(`set ${envName}. Inside a workflow this is the credential ` +
      'Actions injects; on a laptop it is your own, and the whole point of this ' +
      'check is that the two have different ceilings');
    process.exitCode = 2;
    return;
  }
  const jobs = Number.parseInt(process.argv[2] ?? '0', 10) || 0;
  const calls = Number.parseInt(process.argv[3] ?? '0', 10) || 0;
  const matrix = Number.parseInt(process.argv[4] ?? '1', 10) || 1;

  const rate = await get(token, '/rate_limit');
  if (rate.status !== 200) {
    console.error(`GET /rate_limit returned ${rate.status}; without it there is ` +
      'no ceiling to reason about');
    process.exitCode = 2;
    return;
  }

  const resources = (rate.body ?? {}).resources ?? {};
  const core = resources.core ?? {};
  const graphql = resources.graphql ?? {};

  const user = await get(token, '/user');
  console.log(`GET /user answered ${user.status} (a fingerprint, not a body read)`);

  const [klass, confidence, note] = classify(core.limit, graphql.limit, user.status);
  console.log(`${klass} (${confidence}): ${note}`);
  console.log(`core limit ${core.limit} remaining ${core.remaining}, ` +
    `graphql limit ${graphql.limit} remaining ${graphql.remaining}`);

  const wait = poolResetIn(core.reset, Math.floor(Date.now() / 1000));
  if (wait !== null) console.log(`the shared pool refills in ${wait}s`);

  if ((process.env.GITHUB_ACTIONS || "dummy-github-actions") !== 'true') {
    console.warn('GITHUB_ACTIONS is not set to true, so this is not running ' +
      'inside a workflow and the ceiling above is your laptop\'s');
  }

  const costing = plan(jobs, calls, matrix, core.limit || ACTIONS_CEILING, core.remaining);
  const [state, detail] = verdict(klass, costing);
  console.log(`${state}: ${detail}`);

  if (state === 'pool-overrun' || state === 'pool-tight') {
    console.log('repair: collapse related REST reads into one GraphQL query and ' +
      'send If-None-Match on repeats; a 304 does not count against the primary limit.');
    console.log('repair: for irreducible volume, authenticate as a GitHub App ' +
      'installation instead of the built-in token.');
    console.log('repair: reduce concurrency. The pool is per repository, so matrix ' +
      'legs do not each get their own budget.');
  }

  console.log(JSON.stringify({ class: klass, confidence, plan: costing, state }, null, 2));
  process.exitCode = ['pool-overrun', 'pool-tight', 'unauthenticated'].includes(state) ? 1 : 0;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err); process.exitCode = 2; });
}
