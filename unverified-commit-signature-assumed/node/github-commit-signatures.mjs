/**
 * Report what a repository's commit signatures actually say.
 *
 * Read only. One GET per page of commits and one for the branch rules.
 * Nothing is signed and no ruleset is created: where a rule is missing the
 * script prints the request for an admin to action.
 *
 * verification.verified is one field of five. Reading it alone throws away the
 * difference between unsigned, badly signed, well signed by an unregistered
 * key, and not checked at all. A missing verification object is a fifth state
 * and it is not a false one.
 *
 * Environment:
 *   GITHUB_TOKEN      a read-only token that can see the repository
 *   GITHUB_REPO       owner/name
 *   GITHUB_REF        optional branch, tag or sha to walk from
 *   GITHUB_PAGES      optional pages of 100 commits, default 1
 *   GITHUB_BRANCH     optional branch whose rules to read
 *   GITHUB_AUTHORS    optional comma-separated author allowlist
 */
const API = 'https://api.github.com';
const UA = 'github-commit-signatures/1.0';

/** Every documented reason, mapped to the family whose repair it shares. */
export const REASONS = {
  valid: ['verified', 'the signature was checked and the committer identity resolved.'],
  unsigned: ['unsigned', 'the commit object carries no signature at all.'],
  invalid: ['signature-rejected', 'a signature is present and did not verify against the key.'],
  malformed_signature: ['signature-rejected', 'the signature could not be parsed.'],
  expired_key: ['signature-rejected', 'the key that made the signature has expired.'],
  not_signing_key: ['signature-rejected', 'the key is not flagged for signing.'],
  unknown_signature_type: ['signature-rejected', 'the signature is not a type GitHub verifies.'],
  unknown_key: ['identity-not-linked', 'the key that made the signature is not '
    + 'registered to any GitHub account. The cryptography is fine; the account '
    + 'link is missing.'],
  no_user: ['identity-not-linked', 'no GitHub account owns the committer email address.'],
  unverified_email: ['identity-not-linked', 'the committer email belongs to an '
    + 'account and has not been verified on it.'],
  bad_email: ['identity-not-linked', 'the committer email is not among the '
    + 'identities on the key.'],
  gpgverify_error: ['github-could-not-check', "GitHub's verification service "
    + 'errored. This is not a statement about the commit.'],
  gpgverify_unavailable: ['github-could-not-check', "GitHub's verification "
    + 'service was unavailable. This is not a statement about the commit.'],
};

export const FAMILIES = ['verified', 'unsigned', 'signature-rejected',
  'identity-not-linked', 'github-could-not-check', 'verification-absent',
  'unknown-reason'];

/** Families that are a finding about this repository. Excludes the outage one. */
export const VIOLATIONS = ['unsigned', 'signature-rejected'];

/** REST requests this run will spend. Pure. */
export function readCost(pages, withRules) {
  return Math.max(1, Number(pages) || 1) + (withRules ? 1 : 0);
}

/** Normalise one commit's verification object. Pure. */
export function verificationOf(commit) {
  const inner = (commit && commit.commit) || {};
  const raw = inner.verification;
  if (!raw || typeof raw !== 'object') {
    return { present: false, verified: null, reason: null, hasSignature: false, verifiedAt: null };
  }
  return {
    present: true,
    verified: raw.verified,
    reason: raw.reason,
    hasSignature: Boolean(raw.signature),
    verifiedAt: raw.verified_at ?? null,
  };
}

/** Sort one normalised verification into its family. Pure. [family, detail]. */
export function familyOf(verification) {
  if (!verification || !verification.present) {
    return ['verification-absent', 'this payload carried no verification '
      + 'object. That is unknown, not unsigned, and it must not be counted as '
      + 'either.'];
  }
  const { reason, verified } = verification;
  if (reason === null || reason === undefined) {
    return ['unknown-reason', 'the verification object has no reason field, so '
      + 'the boolean is the only evidence and it is not enough to act on.'];
  }
  const known = REASONS[String(reason)];
  if (!known) {
    return ['unknown-reason', `reason ${JSON.stringify(reason)} is not one this `
      + 'script knows. Report it rather than letting it fall into a default.'];
  }
  const [family, detail] = known;
  if (family === 'verified' && verified !== true) {
    return ['unknown-reason', 'reason is valid and verified is not true, which '
      + 'is a shape GitHub does not normally produce. Treat it as unknown.'];
  }
  if (family !== 'verified' && verified === true) {
    return ['unknown-reason', `verified is true beside reason `
      + `${JSON.stringify(reason)}. Only valid accompanies a true, so this pair `
      + 'is not readable.'];
  }
  return [family, detail];
}

/** What the commit says about who wrote it. Pure. [state, detail]. */
export function identitySplit(commit) {
  const inner = (commit && commit.commit) || {};
  const authorEmail = ((inner.author && inner.author.email) || '');
  const committerEmail = ((inner.committer && inner.committer.email) || '');
  const linkedAuthor = commit ? commit.author : null;
  const linkedCommitter = commit ? commit.committer : null;
  if (!authorEmail && !committerEmail) {
    return ['no-emails', 'the commit carries no author or committer email to compare.'];
  }
  if (authorEmail.toLowerCase() !== committerEmail.toLowerCase()) {
    return ['author-differs-from-committer', 'the author and the committer are '
      + 'different identities, and a signature speaks for the committer. A '
      + 'verified commit here does not assert the author consented to it.'];
  }
  if (!linkedAuthor || !linkedCommitter) {
    return ['email-resolves-to-no-account', 'an email on this commit resolves '
      + 'to no GitHub account, so there is no account for a signature to be '
      + 'matched against.'];
  }
  return ['author-is-committer', 'author and committer are the same identity '
    + 'and both resolve to GitHub accounts.'];
}

/** The check people actually wrote. Pure. true, false or null. */
export function authorAllowlistPass(commit, allowed) {
  if (!allowed || allowed.length === 0) return null;
  const inner = (commit && commit.commit) || {};
  const email = (((inner.author && inner.author.email) || '')).toLowerCase();
  const set = new Set(allowed.map((a) => String(a).trim().toLowerCase()).filter(Boolean));
  return set.has(email);
}

/** The check the policy meant. Pure. true, false or null for unknown. */
export function signaturePass(commit) {
  const [family] = familyOf(verificationOf(commit));
  if (family === 'verified') return true;
  if (VIOLATIONS.includes(family) || family === 'identity-not-linked') return false;
  return null;
}

/** Where the two checks differ, commit by commit. Pure. */
export function disagreements(commits, allowed) {
  const out = [];
  for (const commit of commits || []) {
    const naive = authorAllowlistPass(commit, allowed);
    const careful = signaturePass(commit);
    if (naive === null || naive === careful) continue;
    out.push({
      sha: commit ? commit.sha : null,
      author_check: naive,
      signature_check: careful,
      gap: naive ? 'author-passed-signature-did-not' : 'signature-passed-author-did-not',
    });
  }
  return out;
}

/** Count the families across a list of commits. Pure. */
export function tally(commits) {
  const counts = {};
  for (const name of FAMILIES) counts[name] = 0;
  for (const commit of commits || []) {
    const [family] = familyOf(verificationOf(commit));
    counts[family] = (counts[family] || 0) + 1;
  }
  return counts;
}

/** Is a signature rule actually in force on the branch. Pure. [state, detail]. */
export function enforcementFromRules(rules, readable = true) {
  if (!readable) {
    return ['rule-unreadable', 'the branch rules could not be read with this '
      + 'token, so whether signatures are enforced is unknown. That is not the '
      + 'same as unenforced.'];
  }
  if (!Array.isArray(rules)) {
    return ['rule-unreadable', 'the rules endpoint did not return a list, so '
      + 'nothing can be concluded about enforcement.'];
  }
  for (const rule of rules) {
    if (rule && rule.type === 'required_signatures') {
      return ['enforced', 'a required_signatures rule is active on this branch, '
        + 'so an unsigned push is rejected rather than reported.'];
    }
  }
  return ['no-rule', 'no required_signatures rule is active on this branch. '
    + 'Whatever the history shows, the next push is free to be unsigned.'];
}

/** The finding, in one word. Pure. [state, detail]. */
export function grade(counts, enforcementState) {
  const c = counts || {};
  if (c['verification-absent']) {
    return ['verification-unknown', `${c['verification-absent']} commit(s) `
      + 'arrived with no verification object. Until that is understood, no '
      + 'percentage from this run is trustworthy.'];
  }
  const violations = VIOLATIONS.reduce((n, k) => n + (c[k] || 0), 0);
  if (violations) {
    return ['unsigned-or-rejected-present', `${violations} commit(s) are `
      + 'unsigned or carry a signature that did not verify. This is the finding '
      + 'a signed-commit policy exists to produce.'];
  }
  if (c['identity-not-linked']) {
    return ['identity-not-linked-present', `${c['identity-not-linked']} commit(s) `
      + 'carry a good signature from a key no GitHub account claims. Nothing is '
      + 'cryptographically wrong; a public key needs uploading.'];
  }
  if (c['unknown-reason']) {
    return ['unreadable-verification', `${c['unknown-reason']} commit(s) have a `
      + 'verification shape this script does not recognise. Report them rather '
      + 'than grading them.'];
  }
  if (c['github-could-not-check']) {
    return ['checker-unavailable', `${c['github-could-not-check']} commit(s) `
      + 'could not be checked by GitHub. That is an outage, not a violation, '
      + 'and re-reading later is the whole response.'];
  }
  if (enforcementState === 'enforced') {
    return ['verified-and-enforced', 'every commit read is verified and a rule '
      + 'requires it, which is the only combination that is a guarantee.'];
  }
  return ['verified-but-not-enforced', 'every commit read is verified and '
    + 'nothing requires it. That is a description of past behaviour, not a '
    + 'constraint on the next push.'];
}

/** The sentence a reader has to act on. Pure. Nothing here is executed. */
export function repair(state, enforcementState, repo, branch) {
  const lines = [];
  if (state === 'unsigned-or-rejected-present') {
    lines.push('find the commits listed as unsigned or signature-rejected and '
      + 'get them re-signed or reverted');
  }
  if (state === 'identity-not-linked-present') {
    lines.push('ask the key owners to add their public keys to their GitHub '
      + 'accounts; the signatures are already good');
  }
  if (state === 'verification-unknown') {
    lines.push('find out why a verification object was missing before reporting '
      + 'any signing percentage from this repository');
  }
  if (state === 'checker-unavailable') {
    lines.push('re-read later: GitHub could not check these commits and that is '
      + 'not a fact about your repository');
  }
  if (enforcementState === 'no-rule') {
    lines.push(`ask an admin of ${repo} to add a ruleset requiring signed `
      + `commits on ${branch || 'the default branch'}, so unsigned pushes are `
      + 'rejected rather than reported');
  }
  if (enforcementState === 'rule-unreadable') {
    lines.push(`re-run with a token that can read branch rules on ${repo}, `
      + 'because unreadable is not unenforced');
  }
  if (lines.length === 0) lines.push('nothing to repair from this reading');
  return `${lines.join('. ')}. Nothing here writes.`;
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
  const repo = (process.env.GITHUB_REPO || "dummy-github-repo");
  if (!token || !repo) {
    console.error('set GITHUB_TOKEN (read-only is enough) and GITHUB_REPO');
    process.exitCode = 2;
    return;
  }
  const pages = Number((process.env.GITHUB_PAGE || "dummy-github-page")S || '1') || 1;
  const branch = (process.env.GITHUB_BRANC || "dummy-github-branc")H || '';
  const ref = (process.env.GITHUB_RE || "dummy-github-re")F || '';
  const allowed = ((process.env.GITHUB_AUTHOR || "dummy-github-author")S || '').split(',')
    .map((s) => s.trim()).filter(Boolean);
  console.log(`read cost: ${readCost(pages, Boolean(branch))} REST request(s) `
    + 'against the core hourly quota');

  const commits = [];
  for (let page = 1; page <= Math.max(1, pages); page += 1) {
    let path = `/repos/${repo}/commits?per_page=100&page=${page}`;
    if (ref) path += `&sha=${ref}`;
    const response = await fetch(`${API}${path}`, { headers: headers(token) });
    if (response.status !== 200) {
      console.error(`GET ${path} -> HTTP ${response.status}; stopping`);
      break;
    }
    const batch = await response.json();
    if (!Array.isArray(batch) || batch.length === 0) break;
    commits.push(...batch);
  }
  console.log(`${commits.length} commit(s) read from ${repo}`);

  const counts = tally(commits);
  console.log(`verified: ${counts.verified}  unsigned: ${counts.unsigned}  `
    + `signature-rejected: ${counts['signature-rejected']}  `
    + `identity-not-linked: ${counts['identity-not-linked']}  `
    + `github-could-not-check: ${counts['github-could-not-check']}  `
    + `verification-absent: ${counts['verification-absent']}`);

  const gaps = disagreements(commits, allowed);
  if (allowed.length) {
    const missed = gaps.filter((g) => g.gap === 'author-passed-signature-did-not');
    console.log(`author-check-disagreement: ${missed.length} commit(s) the `
      + 'author allowlist passed and the signature check did not');
  }

  const splits = {};
  for (const commit of commits) {
    const [state] = identitySplit(commit);
    splits[state] = (splits[state] || 0) + 1;
  }
  console.log(`identity: ${JSON.stringify(splits)}`);

  let rules = null;
  let readable = false;
  if (branch) {
    const response = await fetch(`${API}/repos/${repo}/rules/branches/${branch}`,
      { headers: headers(token) });
    readable = response.status === 200;
    rules = readable ? await response.json() : null;
  }
  const [enforcementState, enforcementDetail] = enforcementFromRules(
    rules, branch ? readable : false,
  );
  if (branch) console.log(`enforcement: ${enforcementState}. ${enforcementDetail}`);

  const [state, detail] = grade(counts, enforcementState);
  console.log(`${state}: ${detail}`);
  const fix = repair(state, branch ? enforcementState : 'not-read', repo, branch);
  console.log(`repair: ${fix}`);

  console.log(JSON.stringify({
    repository: repo,
    commits_read: commits.length,
    counts,
    identity_split: splits,
    disagreements: gaps.slice(0, 20),
    disagreement_count: gaps.length,
    enforcement_state: branch ? enforcementState : 'not-read',
    state,
    detail,
    repair: fix,
  }, null, 2));
  process.exitCode = ['unsigned-or-rejected-present', 'verification-unknown',
    'identity-not-linked-present', 'verified-but-not-enforced'].includes(state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
