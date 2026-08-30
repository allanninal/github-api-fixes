/**
 * Audit branch protection without mistaking a refusal for an absence.
 *
 * Read only. Three GETs per branch and nothing is written: no commit, no ref
 * update, no settings change. What a push would be refused for is derived from
 * the rules the API publishes, never by attempting a push.
 *
 * The detailed protection rules need repository admin. Without it,
 * GET .../protection answers 403, and only a 404 whose message is "Branch not
 * protected" is evidence of absence. The protected boolean on the branch and
 * the ruleset rules for the branch are both readable without admin.
 *
 * Environment:
 *   GITHUB_TOKEN      a token with read access to the repositories
 *   GITHUB_BRANCHES   comma-separated owner/repo:branch values
 */
const API = 'https://api.github.com';
const UA = 'github-branch-protection-audit/1.0';

/** The one message that turns a 404 into a finding. */
export const ABSENCE_MESSAGE = 'branch not protected';

/** Three reads per branch: the branch, the protection, the ruleset rules. */
export const REQUESTS_PER_BRANCH = 3;

/** Whether this answer is evidence that the branch is unprotected. Pure. */
export function isAbsence(status, message) {
  const code = Number(status);
  if (!Number.isFinite(code) || code !== 404) return false;
  return String(message ?? '').toLowerCase().includes(ABSENCE_MESSAGE);
}

/** What the protection endpoint's answer tells you. Pure. */
export function visibility(status, message) {
  const code = Number(status);
  if (!Number.isFinite(code)) return 'unknown';
  if (code === 200) return 'readable';
  if (isAbsence(code, message)) return 'not-protected';
  if (code === 403) return 'admin-required';
  if (code === 404) return 'ambiguous-404';
  return 'unknown';
}

/** Classify one branch from all three readings. Pure. [state, detail]. */
export function verdict(protectedFlag, status, message, rules = []) {
  const seen = visibility(status, message);
  const ruleCount = Array.isArray(rules) ? rules.length : 0;

  if (protectedFlag === null || protectedFlag === undefined) {
    return ['branch-unreadable', 'the branch itself did not come back, so there '
      + 'is nothing to judge. That is a repository or credential problem rather '
      + 'than a protection one.'];
  }

  if (protectedFlag) {
    if (seen === 'readable') {
      return ['protected-rules-readable', 'the branch is protected and this '
        + 'token can read the rules, so the refusals below are quoted from '
        + 'settings rather than inferred.'];
    }
    if (seen === 'admin-required') {
      let detail = 'the branch reports protected=true and the protection '
        + 'endpoint refused with admin rights required, so the classic rules '
        + 'are not readable by this token.';
      if (ruleCount) {
        detail += ` ${ruleCount} ruleset rule(s) are readable and are reported.`;
      }
      return ['protected-rules-hidden', detail];
    }
    if (seen === 'not-protected') {
      return ['contradictory', 'the branch says protected=true and the '
        + 'protection endpoint says the branch is not protected. A ruleset '
        + 'governs this branch without classic branch protection behind it.'];
    }
    return ['protected-rules-hidden', `the branch reports protected=true and `
      + `the protection endpoint answered ${status}, which is not a readable `
      + 'rule set. Treat this as protected and unmeasured.'];
  }

  if (ruleCount) {
    return ['ruleset-only', `protected=false, but ${ruleCount} rule(s) reach `
      + 'this branch from a ruleset. Classic protection is not the only thing '
      + 'that refuses a push.'];
  }
  if (seen === 'not-protected') {
    return ['unprotected-confirmed', 'protected=false and the protection '
      + 'endpoint answered 404 Branch not protected, which is the one 404 that '
      + 'means absence.'];
  }
  if (seen === 'admin-required') {
    return ['unprotected-by-flag', 'protected=false on the branch object, which '
      + 'is visible without admin and is the honest reading. The protection '
      + 'endpoint refused separately and adds nothing here.'];
  }
  return ['unknown', `protected=false but the protection endpoint answered `
    + `${status} rather than a recognised absence, so this row is not resolved.`];
}

/** Plain statements of what the classic rules refuse. Pure. */
export function refusedWrites(protection) {
  if (!protection || typeof protection !== 'object') return [];
  const out = [];
  const reviews = protection.required_pull_request_reviews;
  if (reviews && typeof reviews === 'object') {
    const count = reviews.required_approving_review_count;
    if (count) {
      out.push(`a direct push is refused: ${count} approving review(s) are `
        + 'required through a pull request');
    } else {
      out.push('a direct push is refused: a pull request is required');
    }
  }
  const checks = protection.required_status_checks;
  if (checks && typeof checks === 'object') {
    const contexts = checks.contexts || [];
    out.push(`a merge is refused until ${contexts.length} status check(s) pass`);
    if (checks.strict) {
      out.push('a merge is refused while the branch is behind its base');
    }
  }
  if ((protection.enforce_admins || {}).enabled) {
    out.push('administrators are not exempt from any of the above');
  }
  const restrictions = protection.restrictions;
  if (restrictions && typeof restrictions === 'object') {
    const actors = (restrictions.users || []).length
      + (restrictions.teams || []).length + (restrictions.apps || []).length;
    out.push(`a push is refused for everyone except ${actors} listed actor(s)`);
  }
  if ((protection.required_signatures || {}).enabled) {
    out.push('an unsigned commit is refused');
  }
  if ((protection.lock_branch || {}).enabled) {
    out.push('the branch is locked, so every write is refused');
  }
  const force = protection.allow_force_pushes;
  if (force && typeof force === 'object' && !force.enabled) {
    out.push('a force push is refused');
  }
  const deletions = protection.allow_deletions;
  if (deletions && typeof deletions === 'object' && !deletions.enabled) {
    out.push('deleting the branch is refused');
  }
  return out;
}

/** The same statements, from the ruleset listing. Pure. */
export function refusedByRules(rules) {
  if (!Array.isArray(rules)) return [];
  const kinds = rules.filter((r) => r && typeof r === 'object').map((r) => r.type);
  const out = [];
  if (kinds.includes('pull_request')) {
    out.push('a pull request is required, so a direct push to this branch is refused');
  }
  if (kinds.includes('required_status_checks')) {
    out.push("a merge is refused until the ruleset's status checks pass");
  }
  if (kinds.includes('non_fast_forward')) {
    out.push('non-fast-forward updates are blocked, so a force push is refused');
  }
  if (kinds.includes('deletion')) out.push('deleting the branch is refused');
  if (kinds.includes('creation')) out.push('creating this ref is refused');
  if (kinds.includes('update')) out.push('updating this ref directly is refused');
  if (kinds.includes('required_signatures')) out.push('an unsigned commit is refused');
  return out;
}

/** Which rulesets contributed the rules, for the report. Pure. */
export function rulesetsNamed(rules) {
  const names = [];
  for (const rule of rules || []) {
    if (!rule || typeof rule !== 'object') continue;
    const source = rule.ruleset_source || rule.ruleset_source_type;
    if (source && !names.includes(source)) names.push(source);
  }
  return names;
}

/** Who is allowed to push to a restricted branch. Pure. Names only. */
export function pushAllowlist(protection) {
  const restrictions = (protection || {}).restrictions;
  if (!restrictions || typeof restrictions !== 'object') return [];
  const out = [];
  for (const user of restrictions.users || []) {
    if (user && user.login) out.push(`user:${user.login}`);
  }
  for (const team of restrictions.teams || []) {
    if (team && team.slug) out.push(`team:${team.slug}`);
  }
  for (const app of restrictions.apps || []) {
    if (app && app.slug) out.push(`app:${app.slug}`);
  }
  return out;
}

/** Summarise a sweep without letting unknown become unprotected. Pure. */
export function coverage(states) {
  const counts = {
    protected: 0, readable_in_detail: 0, unprotected: 0, unknown: 0,
  };
  for (const state of states || []) {
    if (['protected-rules-readable', 'protected-rules-hidden', 'contradictory',
      'ruleset-only'].includes(state)) {
      counts.protected += 1;
      if (state === 'protected-rules-readable') counts.readable_in_detail += 1;
    } else if (['unprotected-confirmed', 'unprotected-by-flag'].includes(state)) {
      counts.unprotected += 1;
    } else {
      counts.unknown += 1;
    }
  }
  return counts;
}

/** Whether the sweep measured the estate or measured its own token. Pure. */
export function instrumentVerdict(counts) {
  const c = counts || {};
  const protectedCount = Number(c.protected) || 0;
  const detail = Number(c.readable_in_detail) || 0;
  const unknown = Number(c.unknown) || 0;
  const total = protectedCount + (Number(c.unprotected) || 0) + unknown;
  if (!total) return ['no-rows', 'nothing was checked.'];
  if (unknown) {
    return ['instrument-gap', `${unknown} of ${total} row(s) are unresolved. `
      + 'Those are not findings about the estate.'];
  }
  if (protectedCount && !detail) {
    return ['coverage-only', 'every protected branch was counted from its '
      + 'boolean and none of the classic rules were readable. Coverage is '
      + 'trustworthy, detail is absent.'];
  }
  return ['measured', 'every row resolved to a state about the branch.'];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (state === 'protected-rules-hidden') {
    return 'report this as protected. To read the detailed rules, grant this '
      + 'token repository admin or use an App with administration: read.';
  }
  if (state === 'protected-rules-readable') {
    return 'nothing on visibility. Check the rules against your policy; the '
      + 'refusals above are what a push actually meets.';
  }
  if (state === 'unprotected-confirmed') {
    return 'this branch really is unprotected. Protect it or record the exception.';
  }
  if (state === 'unprotected-by-flag') {
    return 'this branch is unprotected on the boolean that needs no admin. Do '
      + 'not upgrade the token to confirm an absence you can already see.';
  }
  if (state === 'ruleset-only') {
    return 'read the ruleset rather than the branch protection settings. A '
      + 'ruleset refuses pushes without setting protected=true.';
  }
  if (state === 'contradictory') {
    return 'audit the ruleset that governs this branch. Classic protection is '
      + 'not what is refusing writes here.';
  }
  if (state === 'branch-unreadable') {
    return 'triage the repository and the token before the protection: check '
      + 'the name, the visibility and the installation.';
  }
  return 'record this row as unknown. An unresolved answer is not a finding and '
    + 'must never be counted as unprotected.';
}

/** Requests this run will spend against the core quota. Pure. */
export function readCost(branches) {
  return REQUESTS_PER_BRANCH * ((branches || []).length);
}

/** owner/repo:branch into its three parts. Pure. */
export function splitTarget(target) {
  const text = String(target ?? '').trim();
  const at = text.lastIndexOf(':');
  const repo = at > 0 ? text.slice(0, at) : text;
  const branch = at > 0 ? text.slice(at + 1) : 'main';
  const parts = repo.split('/');
  if (parts.length !== 2 || !parts[0] || !parts[1] || !branch) return null;
  return [parts[0], parts[1], branch];
}

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
}

async function getJson(token, path) {
  const res = await fetch(`${API}${path}`, { headers: headers(token) });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return { status: res.status, body };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const raw = (process.env.GITHUB_BRANCHES || "dummy-github-branches");
  if (!token || !raw) {
    console.error('set GITHUB_TOKEN (read-only is enough) and GITHUB_BRANCHES');
    process.exitCode = 2;
    return;
  }
  const targets = raw.split(',').map((t) => splitTarget(t)).filter(Boolean);
  if (!targets.length) {
    console.error('GITHUB_BRANCHES should hold owner/repo:branch values');
    process.exitCode = 2;
    return;
  }

  console.log(`read cost: at most ${REQUESTS_PER_BRANCH} request(s) per branch `
    + 'against the core hourly quota');
  console.log(`read cost: at most ${readCost(targets)} request(s) in total`);

  const findings = [];
  for (const [owner, name, branch] of targets) {
    const base = `/repos/${owner}/${name}`;
    const b = await getJson(token, `${base}/branches/${branch}`);
    const flag = b.status === 200 && b.body ? Boolean(b.body.protected) : null;
    const p = await getJson(token, `${base}/branches/${branch}/protection`);
    const protection = p.status === 200 && p.body ? p.body : null;
    const r = await getJson(token, `${base}/rules/branches/${branch}`);
    const rules = r.status === 200 && Array.isArray(r.body) ? r.body : [];

    const message = p.body && typeof p.body === 'object' ? p.body.message : '';
    const [state, detail] = verdict(flag, p.status, message, rules);
    const refusals = refusedWrites(protection).length
      ? refusedWrites(protection) : refusedByRules(rules);
    const label = `${owner}/${name}:${branch}`;

    console.log(`${label} protected=${flag} protection=${p.status} rules=${rules.length}`);
    console.log(`${state}: ${detail}`);
    for (const line of refusals) console.log(`  ${line}`);
    console.log(`repair: ${repair(state)}`);

    findings.push({
      branch: label,
      protected: flag,
      protection_status: p.status,
      protection_visibility: visibility(p.status, message),
      ruleset_rule_count: rules.length,
      ruleset_sources: rulesetsNamed(rules),
      refused_writes: refusals,
      push_allowlist: pushAllowlist(protection),
      state,
      detail,
      repair: repair(state),
    });
  }

  const counts = coverage(findings.map((f) => f.state));
  const [instrument, note] = instrumentVerdict(counts);
  console.log(`summary: ${counts.protected} protected, `
    + `${counts.readable_in_detail} readable in detail, ${counts.unprotected} `
    + `unprotected, ${counts.unknown} unknown`);
  console.log(`${instrument}: ${note}`);

  console.log(JSON.stringify({
    requests_spent_at_most: readCost(targets),
    coverage: counts,
    instrument: { state: instrument, detail: note },
    findings,
  }, null, 2));
  const bad = ['unprotected-confirmed', 'unprotected-by-flag'];
  process.exitCode = counts.unknown
    || findings.some((f) => bad.includes(f.state)) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
