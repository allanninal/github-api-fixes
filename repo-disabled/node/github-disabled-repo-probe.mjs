/**
 * Recognise a disabled repository and keep its zeroes out of your aggregates.
 *
 * Read only. One GET for the repository object and one cheap GET per probed
 * sub-resource, all at per_page=1. Nothing is written and no write is attempted
 * to characterise the state.
 *
 * A disabled repository keeps appearing in organisation listings and keeps
 * serving its own repository object while most of its sub-resources stop
 * answering, so an org-wide sweep records it as zero of everything.
 *
 * Environment:
 *   GITHUB_TOKEN      a token with read access to the repositories
 *   GITHUB_REPOS      comma-separated owner/name values
 */
const API = 'https://api.github.com';
const UA = 'github-disabled-repo-probe/1.0';

/** Cheap reads a repository normally answers. */
export const DEFAULT_PROBES = ['/branches?per_page=1', '/commits?per_page=1',
  '/contributors?per_page=1', '/languages'];

/** An empty repository answers this on anything that needs a commit. */
export const EMPTY_REPOSITORY = 409;

/** Which platform state this repository is in. Pure. */
export function platformState(repo) {
  if (!repo || typeof repo !== 'object') return 'unknown';
  const disabled = Boolean(repo.disabled);
  const archived = Boolean(repo.archived);
  if (disabled && archived) return 'disabled-and-archived';
  if (disabled) return 'disabled';
  if (archived) return 'archived';
  return 'active';
}

/** Whether the disabled boolean is set, in either combination. Pure. */
export function isDisabled(state) {
  return state === 'disabled' || state === 'disabled-and-archived';
}

/** Whether the repository state accounts for this answer. Pure. */
export function explainsSubresource(state, status) {
  const code = Number(status);
  if (!Number.isFinite(code)) return [false, 'no readable status for this probe.'];
  if (code >= 200 && code < 300) return [true, 'answered'];
  if (code === EMPTY_REPOSITORY) {
    return [false, '409, which is an empty repository rather than a disabled one'];
  }
  if (isDisabled(state) && [403, 404, 451].includes(code)) {
    return [true, 'explained by the disabled state'];
  }
  if (state === 'archived' && [403, 404].includes(code)) {
    return [false, 'not explained by archiving, which leaves reads working'];
  }
  return [false, 'not explained by the repository state'];
}

/** Classify a repository from its state and its probe answers. Pure. */
export function probeVerdict(state, probes) {
  const rows = (probes || []).filter((p) => p && typeof p === 'object');
  const failing = rows.filter((p) => {
    const code = Number(p.status);
    return !explainsSubresource(state, p.status)[0]
      || !(Number.isFinite(code) && code >= 200 && code < 300);
  });
  const empty = rows.filter((p) => Number(p.status) === EMPTY_REPOSITORY);

  if (state === 'unknown') {
    return ['repository-unreadable', 'the repository object itself did not come '
      + 'back, so this is a credential or name problem rather than a platform state.'];
  }
  if (isDisabled(state)) {
    if (failing.length) {
      return ['ghost-confirmed', `the repository object reads and ${failing.length} `
        + `of ${rows.length} sub-resource(s) do not, which is what disabled looks `
        + 'like from the outside.'];
    }
    return ['disabled-but-answering', 'disabled is set and every probe answered '
      + 'anyway. Trust the boolean: the repository is switched off and must still '
      + 'be excluded from aggregates.'];
  }
  if (empty.length) {
    return ['empty-repository', `${empty.length} probe(s) answered 409 Git `
      + 'Repository is empty. This repository has never been pushed to and is '
      + 'not disabled.'];
  }
  if (state === 'archived') {
    return ['archived-not-disabled', 'archived rather than disabled. Reads work '
      + 'and only writes are refused, which is a different note.'];
  }
  if (failing.length) {
    return ['not-explained-by-state', `${failing.length} sub-resource(s) failed `
      + 'on a repository that is neither disabled nor archived, so the '
      + 'repository state does not explain it.'];
  }
  return ['healthy', 'the repository reads and every sub-resource answered.'];
}

/** Whether this repository may enter an org-wide aggregate. Pure. */
export function aggregateSafety(state) {
  if (isDisabled(state)) {
    return ['exclude', 'every zero this repository contributes is an artefact of '
      + 'the disabled state rather than a measurement.'];
  }
  if (state === 'unknown') {
    return ['exclude', 'the repository could not be read, so it has no values to '
      + 'contribute and its absence should be visible in the report.'];
  }
  if (state === 'archived') {
    return ['include', 'an archived repository is fully readable, so its values '
      + 'are real. Only its writes are refused.'];
  }
  return ['include', 'nothing here disqualifies this repository from a count.'];
}

/** Whether a zero measured on this repository means anything. Pure. */
export function isRealZero(state, value) {
  const number = Number(value);
  if (!Number.isFinite(number) || value === null || value === '') return null;
  if (number !== 0) return null;
  if (isDisabled(state) || state === 'unknown') return false;
  return true;
}

/** What a sweep should report alongside its total. Pure. */
export function aggregateImpact(rows) {
  let counted = 0;
  let excluded = 0;
  let falseZeroes = 0;
  for (const row of rows || []) {
    const state = (row || {}).state;
    const [decision] = aggregateSafety(state);
    if (decision === 'exclude') {
      excluded += 1;
      if (isDisabled(state)) falseZeroes += 1;
    } else {
      counted += 1;
    }
  }
  return { counted, excluded, false_zeroes_avoided: falseZeroes };
}

/** Who can actually change this state. Pure. */
export function remedyOwner(state) {
  if (isDisabled(state)) {
    return 'GitHub, through the billing or support relationship for this '
      + 'account. The API does not say which reason applies.';
  }
  if (state === 'archived') {
    return 'whoever owns the repository, by unarchiving it. That is a decision '
      + 'about whether it is still in use.';
  }
  if (state === 'unknown') return 'nobody yet: the repository could not be read.';
  return 'no remedy needed.';
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (['ghost-confirmed', 'disabled-but-answering'].includes(state)) {
    return 'exclude this repository from org-wide aggregates and report it '
      + 'separately. Nothing in your integration can re-enable it; that is a '
      + 'billing or account matter with GitHub.';
  }
  if (state === 'empty-repository') {
    return 'nothing. A repository that has never been pushed to answers 409 on '
      + 'anything needing a commit, and that is not this problem.';
  }
  if (state === 'archived-not-disabled') {
    return 'see /github/repo-archived-writes-403/ -- reads work there and only '
      + 'writes are refused.';
  }
  if (state === 'not-explained-by-state') {
    return 'triage the failures as a credential problem: the repository state '
      + 'does not account for them.';
  }
  if (state === 'repository-unreadable') {
    return 'check the name, the visibility and the installation before anything '
      + 'else. A 404 on the repository means several things.';
  }
  return 'nothing on the platform state.';
}

/** Requests this run will spend against the core quota. Pure. */
export function readCost(repos, probes = DEFAULT_PROBES) {
  const perRepo = 1 + (probes || []).length;
  return perRepo * ((repos || []).length);
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
    console.error('set GITHUB_TOKEN (read-only is enough) and GITHUB_REPOS');
    process.exitCode = 2;
    return;
  }

  console.log(`read cost: ${1 + DEFAULT_PROBES.length} request(s) per repository `
    + 'against the core hourly quota');
  console.log(`read cost: ${readCost(names)} request(s) in total`);

  const findings = [];
  for (const name of names) {
    const res = await fetch(`${API}/repos/${name}`, { headers: headers(token) });
    let repo = null;
    try { repo = await res.json(); } catch { repo = null; }
    const state = res.status === 200 ? platformState(repo) : 'unknown';

    const probes = [];
    if (res.status === 200) {
      for (const path of DEFAULT_PROBES) {
        const p = await fetch(`${API}/repos/${name}${path}`, { headers: headers(token) });
        const [explained, why] = explainsSubresource(state, p.status);
        probes.push({ path: path.split('?')[0], status: p.status, explained, why });
      }
    }

    const [verdict, detail] = probeVerdict(state, probes);
    const [decision, reason] = aggregateSafety(state);

    console.log(`${name}: disabled=${Boolean((repo || {}).disabled)} `
      + `archived=${Boolean((repo || {}).archived)}`);
    console.log(`${verdict}: ${detail}`);
    for (const row of probes) console.log(`  ${row.path} ${row.status} ${row.why}`);
    console.log(`  aggregates: ${decision}. ${reason}`);
    console.log(`  remedy owner: ${remedyOwner(state)}`);
    console.log(`  repair: ${repair(verdict)}`);

    findings.push({
      repository: name,
      repository_status: res.status,
      platform_state: state,
      probes,
      state: verdict,
      detail,
      aggregate_decision: decision,
      aggregate_reason: reason,
      remedy_owner: remedyOwner(state),
      repair: repair(verdict),
    });
  }

  const impact = aggregateImpact(findings.map((f) => ({ state: f.platform_state })));
  const disabled = findings.filter((f) => isDisabled(f.platform_state)).length;
  console.log(`summary: ${findings.length} repositories, ${disabled} disabled, `
    + `${impact.counted} countable`);

  console.log(JSON.stringify({
    requests_spent: readCost(names),
    aggregate_impact: impact,
    findings,
  }, null, 2));
  process.exitCode = disabled ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
