/**
 * Prove whether the credential is the variable, by running two of them.
 *
 * Read only. One GET per rung per credential, at most eight requests, none of
 * which needs a scope.
 *
 * An expired token, a revoked token and a truncated token all return the same
 * 401, so this does not try to tell them apart. It answers the question that is
 * answerable: did the credential change, or did the world.
 */
const API = 'https://api.github.com';
const UA = 'github-credential-differential/1.0';

/** The rungs this run can probe, in order of what they need. Pure. */
export function ladder(repo = null, org = null) {
  const rungs = [['public', '/'], ['identity', '/user']];
  if (repo) rungs.push(['repository', `/repos/${repo}`]);
  if (org) rungs.push(['organization', `/orgs/${org}`]);
  return rungs;
}

/** Reduce a status code to what it says about a credential. Pure. */
export function outcome(status) {
  const code = Number.parseInt(status, 10);
  if (!Number.isFinite(code) || code === 0) return 'error';
  if (code >= 200 && code < 300) return 'ok';
  if (code === 401) return 'unauthenticated';
  if (code === 403) return 'forbidden';
  if (code === 404) return 'missing';
  return 'other';
}

/**
 * Name the signature of a failure across the ladder. Pure.
 * Uniform against selective is the distinction that carries the note: expiry is
 * total, so a credential that answers 200 to anything has not expired.
 */
export function shape(rows) {
  const results = (rows ?? []).map(([, result]) => result);
  if (!results.length) return 'nothing-probed';
  if (results.every((r) => r === 'ok')) return 'healthy';
  if (results.every((r) => r === 'unauthenticated')) return 'uniform-401';
  if (results.some((r) => r === 'ok')) return 'selective';
  return 'mixed';
}

/** Line the two ladders up rung by rung. Pure. */
export function compare(suspect, control) {
  const lookup = new Map(control ?? []);
  return (suspect ?? []).map(([rung, result]) => {
    const other = lookup.has(rung) ? lookup.get(rung) : null;
    return { rung, suspect: result, control: other, agrees: other !== null && other === result };
  });
}

/** Read the two ladders side by side. Pure. Never says "expired". */
export function diagnose(suspect, control = null) {
  const suspectShape = shape(suspect);

  if (suspectShape === 'nothing-probed') {
    return ['nothing-probed', 'no rungs were run, so there is nothing to compare.'];
  }

  const healthy = ['suspect-healthy',
    'the suspect credential answered 200 on every rung, so whatever is failing ' +
    'is not this credential.'];

  if (!control || !control.length) {
    if (suspectShape === 'healthy') return healthy;
    if (suspectShape === 'uniform-401') {
      return ['no-control',
        'every rung answered 401, including the one that needs no credential at ' +
        'all. That is the signature of a value the server will not accept, and ' +
        'expiry, revocation and a truncated string all produce it identically. ' +
        'Without a second credential run at the same instant, the evidence stops here.'];
    }
    return ['no-control',
      `the suspect failed as ${suspectShape} rather than uniformly, which is not ` +
      'what an expired credential looks like: expiry is total. Without a control ' +
      'credential this cannot be taken further.'];
  }

  const rows = compare(suspect, control);
  const controlShape = shape(control);

  if (suspectShape === 'healthy') return healthy;

  if (suspectShape === 'uniform-401' && controlShape === 'uniform-401') {
    return ['both-dead',
      'both credentials answered 401 on every rung. Two tokens do not expire in ' +
      'the same second, so look at what they share: the store they came from, ' +
      'the network they left by, and the organization that can revoke them together.'];
  }

  if (suspectShape === 'uniform-401' && controlShape === 'healthy') {
    return ['credential-is-the-variable',
      'the suspect answered 401 on every rung including the public one, at the ' +
      'same instant the control answered 200 on all of them. The repository, the ' +
      'organization, the network and your code are eliminated: the credential is ' +
      'the only thing that differs. Expiry is the common reason, and revocation ' +
      'and truncation look identical from here.'];
  }

  if (rows.every((row) => row.agrees)) {
    const failing = rows.filter((row) => row.suspect !== 'ok').map((row) => row.rung);
    return ['resource-changed',
      `both credentials answer identically on every rung, and ${failing.join(', ')} ` +
      'failed for both. The thing that changed is the resource, not the token: a ' +
      'repository renamed, transferred or deleted answers the same way to everybody.'];
  }

  if (suspectShape === 'selective') {
    const differing = rows.filter((row) => !row.agrees)
      .map((row) => `${row.rung} (${row.suspect})`);
    return ['access-not-expiry',
      'the suspect answered 200 on at least one rung, so it has not expired: an ' +
      'expired credential cannot authenticate anything. It differs from the ' +
      `control at ${differing.join(', ')}. Look at what that credential is ` +
      'allowed to reach rather than at its calendar.'];
  }

  return ['mixed',
    `the two credentials fail in different ways (${suspectShape} against ` +
    `${controlShape}), which is neither an expiry nor a changed resource. Report ` +
    'the rungs rather than picking a story.'];
}

async function runLadder(token, rungs) {
  const rows = [];
  for (const [rung, path] of rungs) {
    let status = 0;
    try {
      const res = await fetch(API + path, {
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: 'application/vnd.github+json',
          'X-GitHub-Api-Version': '2022-11-28',
          'User-Agent': UA,
        },
      });
      status = res.status;
    } catch (err) {
      console.error(`GET ${path} failed: ${err.message}`);
    }
    rows.push([rung, outcome(status)]);
  }
  return rows;
}

async function main() {
  const suspectToken = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!suspectToken) {
    console.error('set GITHUB_TOKEN to the credential under suspicion');
    process.exitCode = 2;
    return;
  }
  const repo = process.argv[2] ?? null;
  const org = process.argv[3] ?? null;
  const rungs = ladder(repo, org);

  const suspect = await runLadder(suspectToken, rungs);
  const controlToken = (process.env.GITHUB_CONTROL_TOKEN || "dummy-github-control-token");
  const control = controlToken ? await runLadder(controlToken, rungs) : null;
  if (!controlToken) {
    console.warn('GITHUB_CONTROL_TOKEN is not set. Without a control credential ' +
      'this is a description of a 401 rather than a diagnosis.');
  }

  console.log(`${'rung'.padEnd(14)} ${'suspect'.padEnd(8)} control`);
  for (const row of compare(suspect, control ?? [])) {
    console.log(`${row.rung.padEnd(14)} ${row.suspect.padEnd(8)} ${row.control ?? '-'}`);
  }

  const [state, detail] = diagnose(suspect, control);
  console.log(`${state}: ${detail}`);

  if (state === 'credential-is-the-variable') {
    console.log('repair: re-mint the credential, then record its expiry date in ' +
      'the same place the secret is stored and alert before it.');
    console.log('repair: for unattended automation, authenticate as a GitHub App ' +
      'installation; its one-hour tokens need no calendar entry.');
  }
  if (state === 'both-dead') {
    console.log('repair: look at the secrets store, the egress path and any ' +
      'organization policy that could revoke both at once.');
  }

  console.log(JSON.stringify({ state, suspect, control }, null, 2));
  process.exitCode = state === 'suspect-healthy' ? 0 : 1;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err); process.exitCode = 2; });
}
