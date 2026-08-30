/**
 * Say whether the configured repository is a fork of the one you meant.
 *
 * Read only. GET requests and nothing else, and there is nothing to attempt in
 * any case: the failure mode of this bug is that everything succeeds. A fork is
 * a separate repository with its own issues, releases and branches, so an
 * integration pointed at one answers every call with a 200 and is accurate
 * about the wrong object.
 *
 * Environment:
 *   GITHUB_TOKEN      a token with read access to the repositories
 *   GITHUB_REPO       owner/name the integration is configured with
 *   GITHUB_EXPECT_ID  the repository id your state store recorded
 */
const API = 'https://api.github.com';
const UA = 'github-fork-or-upstream/1.0';

/** Gaps large enough that a human recognises the mistake immediately. */
export const STAR_RATIO_OBVIOUS = 10;
export const PUSH_DAYS_OBVIOUS = 90;

const DAY_MS = 86400000;

/** Requests this run will spend against the core quota. Pure. */
export function readCost(withUpstream = true, withReleases = false) {
  let cost = 1;
  if (withUpstream) cost += 1;
  if (withReleases) cost += withUpstream ? 2 : 1;
  return cost;
}

/** One GitHub timestamp to milliseconds, or null. Pure. */
export function parseTs(value) {
  const ms = Date.parse(String(value ?? ''));
  return Number.isFinite(ms) ? ms : null;
}

/** Whole days from one timestamp to another, or null. Pure. */
export function daysBetween(earlier, later) {
  const a = parseTs(earlier);
  const b = parseTs(later);
  if (a === null || b === null) return null;
  return Math.floor((b - a) / DAY_MS);
}

/** The one boolean the whole note turns on. Pure. */
export function isFork(repo) {
  return Boolean((repo || {}).fork);
}

/** parent and source as they were reported. Pure. */
export function forkChain(repo) {
  const r = repo || {};
  return {
    parent: (r.parent || {}).full_name ?? null,
    source: (r.source || {}).full_name ?? null,
  };
}

/** The repository this one should probably have been. Pure. */
export function upstreamOf(repo) {
  const chain = forkChain(repo);
  return chain.source || chain.parent || null;
}

/** Sort the configured repository. Pure. [state, detail]. */
export function classify(repo, expectedId = null) {
  const r = repo || {};
  const liveId = r.id;
  if (expectedId !== null && expectedId !== undefined && expectedId !== ''
      && liveId !== undefined && liveId !== null
      && Number(expectedId) !== Number(liveId)) {
    return ['id-drift', `the stored id is ${expectedId} and this name now `
      + `resolves to ${liveId}. The name has moved to a different object since `
      + 'you last looked, which nothing else will detect.'];
  }
  if (Object.keys(r).length === 0) return ['unknown', 'no repository object was read.'];
  const chain = forkChain(r);
  if (isFork(r)) {
    if (chain.parent && chain.source && chain.parent !== chain.source) {
      return ['fork-of-fork', `this is a fork of ${chain.parent}, which is `
        + `itself a fork. The root of the network is ${chain.source} and that is `
        + 'almost certainly the repository you want.'];
    }
    return ['fork-as-canonical', 'this repository has fork=true, so it is a '
      + 'separate repository with its own issues, releases and branches. Every '
      + `call against it succeeds and describes it rather than ${upstreamOf(r) || 'the upstream'}.`];
  }
  return ['canonical', 'fork=false, so this is a root repository and not a copy '
    + 'of something else.'];
}

/** The size difference between two repositories. Pure. */
export function divergence(fork, source) {
  const f = fork || {};
  const s = source || {};
  const gap = (key) => {
    const a = f[key];
    const b = s[key];
    if (a === undefined || a === null || b === undefined || b === null) return null;
    return { fork: a, upstream: b, difference: b - a };
  };
  const behind = daysBetween(f.pushed_at, s.pushed_at);
  const starsFork = f.stargazers_count || 0;
  const starsUp = s.stargazers_count || 0;
  return {
    stargazers_count: gap('stargazers_count'),
    open_issues_count: gap('open_issues_count'),
    forks_count: gap('forks_count'),
    pushed_days_behind: behind,
    default_branch: { fork: f.default_branch ?? null, upstream: s.default_branch ?? null },
    obvious: Boolean(starsUp >= STAR_RATIO_OBVIOUS * Math.max(1, starsFork)
      || (behind !== null && behind >= PUSH_DAYS_OBVIOUS)),
  };
}

/** Why an audit of this repository would look uneventful. Pure. */
export function quietAuditReasons(repo, releases = null) {
  const r = repo || {};
  const reasons = [];
  if (r.has_issues === false) {
    reasons.push('issues are disabled on this fork, so issue endpoints answer '
      + '410 rather than an empty list');
  }
  if ((r.open_issues_count || 0) === 0) reasons.push('no open issues');
  if (releases === 0) reasons.push('no releases');
  if ((r.forks_count || 0) === 0) reasons.push('nothing has forked it');
  if (r.archived) reasons.push('the repository is archived');
  return reasons;
}

/** The sentence a reader has to act on. Pure. */
export function repair(state, repo, expectedId = null) {
  const upstream = upstreamOf(repo);
  const liveId = (repo || {}).id;
  if (state === 'fork-as-canonical' || state === 'fork-of-fork') {
    return `point the integration at ${upstream || 'the repository named by source'} `
      + 'and store its id beside the name, so a future rename or substitution is '
      + 'a mismatch rather than a quiet quarter.';
  }
  if (state === 'id-drift') {
    return `stop trusting the name. It resolves to id ${liveId} today and your `
      + `store says ${expectedId}, so confirm which object you meant and rekey `
      + 'the state on the id.';
  }
  if (state === 'canonical') {
    return `nothing on the fork question. Store id ${liveId} alongside the name `
      + 'anyway; it is the only key that survives a rename.';
  }
  return 'read the repository first; there is nothing to judge yet.';
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
  const expectId = (process.env.GITHUB_EXPECT_I || "dummy-github-expect-i")D || null;
  console.log(`read cost: ${readCost(true)} request(s) against the core hourly quota`);

  const res = await fetch(`${API}/repos/${repoName}`, { headers: headers(token) });
  if (res.status !== 200) {
    console.error(`${repoName}: HTTP ${res.status} reading the repository`);
    process.exitCode = 2;
    return;
  }
  const repo = await res.json();
  const chain = forkChain(repo);
  console.log(`${repoName}: fork=${repo.fork} id=${repo.id} pushed_at=${repo.pushed_at}`);
  console.log(`  parent=${chain.parent} source=${chain.source}`);

  const [state, detail] = classify(repo, expectId);
  console.log(`${state}: ${detail}`);

  let gaps = null;
  const upstream = upstreamOf(repo);
  if (upstream) {
    const up = await fetch(`${API}/repos/${upstream}`, { headers: headers(token) });
    if (up.status === 200) {
      gaps = divergence(repo, await up.json());
      console.log(`gap against ${upstream}: stars `
        + `${gaps.stargazers_count?.fork} vs ${gaps.stargazers_count?.upstream}, `
        + `last push ${gaps.pushed_days_behind} day(s) behind`);
    }
  }

  const reasons = quietAuditReasons(repo);
  if (reasons.length) console.log(`quiet-audit-explained: ${reasons.join('; ')}`);
  console.log(`repair: ${repair(state, repo, expectId)}`);

  console.log(JSON.stringify({
    configured: repoName,
    id: repo.id,
    node_id: repo.node_id,
    fork: repo.fork,
    chain,
    upstream,
    state,
    detail,
    divergence: gaps,
    quiet_audit_reasons: reasons,
    repair: repair(state, repo, expectId),
  }, null, 2));
  process.exitCode = ['fork-as-canonical', 'fork-of-fork', 'id-drift'].includes(state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
