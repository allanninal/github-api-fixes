/**
 * Say whether the credential doing your automation is a person.
 *
 * Read only. Three GETs at most: the credential's own profile, the
 * organizations it reaches, and optionally the recent commits of one
 * repository. Nothing is created, renamed or revoked.
 *
 * This script reads the response body rather than the response headers.
 * Scopes and expiry are different questions; the one here is whose access this
 * credential borrows.
 */
const API = 'https://api.github.com';
const UA = 'github-actor-identity/1.0';

/** Login fragments that suggest a machine, matched as whole tokens. */
export const MACHINE_HINTS = new Set([
  'bot', 'bots', 'ci', 'cd', 'svc', 'service', 'serviceaccount', 'machine',
  'automation', 'deploy', 'deployer', 'robot', 'jenkins', 'buildbot',
  'integration', 'noreply', 'actions', 'runner',
]);

/** What stays coupled to a human being, per verdict. */
export const COUPLINGS = {
  'personal-account': [
    'commits, comments and reviews are attributed to this person in the ' +
    'history, permanently',
    'deprovisioning the account kills every token on it, without warning and ' +
    'without naming what breaks',
    "removing them from an organization removes the automation's access to it " +
    'on the same afternoon',
    'an expired SAML single sign-on session stops the token mid-run',
    'their two-factor changes, device losses and password resets are all in ' +
    'the failure path',
  ],
  'mixed-signals': [
    'the login is machine shaped and the profile is not, which usually means ' +
    "a person's account was renamed or a shared login sits on somebody's mailbox",
    'whoever controls that mailbox controls the credential',
  ],
  'machine-account': [
    'it still consumes a seat and still needs two-factor authentication',
    'it still needs SAML single sign-on authorization per organization',
    'its password and recovery codes live somewhere, and that somewhere needs ' +
    'an owner who is not one person',
  ],
  'unclassified-user': [
    'the account is a User with nothing that says who owns it, which is the ' +
    'state that produces an unattributable credential',
  ],
};

/** Normalise the GET /user body into the fields that matter. Pure. */
export function identity(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body) || !body.login) {
    return null;
  }
  return {
    login: String(body.login),
    type: String(body.type ?? 'Unknown'),
    name: body.name ?? null,
  };
}

/** Whether a profile name reads as a personal name. Pure. */
export function looksLikeAPersonName(value) {
  const parts = String(value ?? '').replace(/\./g, ' ').split(/\s+/).filter(Boolean);
  if (parts.length < 2) return false;
  return parts.every((p) => /^[A-Za-z]+$/.test(p) && p[0] === p[0].toUpperCase());
}

/** Whether a login was plainly created for a machine. Pure. */
export function machineShaped(login, declared = []) {
  const name = String(login ?? '').toLowerCase();
  if (declared.map((d) => String(d).toLowerCase()).includes(name)) return true;
  if (name.endsWith('[bot]')) return true;
  const tokens = new Set(name.split(/[^a-z0-9]+/));
  for (const token of tokens) if (MACHINE_HINTS.has(token)) return true;
  return false;
}

/**
 * Evidence that this account belongs to a person. Pure.
 * The email address is counted and never quoted.
 */
export function humanSignals(body) {
  const found = [];
  if (!body || typeof body !== 'object') return found;
  if (looksLikeAPersonName(body.name)) found.push(`a personal name is set: ${body.name}`);
  if (body.bio) found.push('a bio is set');
  if (body.hireable) found.push('hireable is set, which no service account needs');
  if (body.email) found.push('a public email address is set');
  if (body.twitter_username) found.push('a social handle is set');
  if (Number.isInteger(body.followers) && body.followers >= 5) {
    found.push(`${body.followers} followers`);
  }
  return found;
}

/** Sort a credential's identity into one of six states. Pure. */
export function classify(ident, signals, machine) {
  if (!ident) {
    return ['identity-unreadable',
      'the credential could not answer GET /user, which is what an ' +
      'installation access token does: it has no user behind it. That is the ' +
      'healthy answer to the question this script asks, and it is also why ' +
      'some endpoints refuse such tokens.'];
  }
  if (ident.type === 'Bot' || ident.login.toLowerCase().endsWith('[bot]')) {
    return ['app-installation',
      `${ident.login} is a Bot identity, so the work is done by a GitHub App ` +
      'installation rather than by a person. Nothing here is coupled to ' +
      "anyone's employment."];
  }
  if (signals.length && machine) {
    return ['mixed-signals',
      `${ident.login} is named like a machine account and carries ` +
      `${signals.length} human signal(s): ${signals.join('; ')}. Usually a ` +
      "person's account renamed, or a shared login on one person's mailbox."];
  }
  if (signals.length) {
    return ['personal-account',
      `${ident.login} is a ${ident.type} with ${signals.length} human ` +
      `signal(s): ${signals.join('; ')}. The automation is running as a person.`];
  }
  if (machine) {
    return ['machine-account',
      `${ident.login} is named like a machine account and carries no human ` +
      'signals. Better than a colleague\'s token, and still an account with ' +
      'a seat, a password and an SSO state.'];
  }
  return ['unclassified-user',
    `${ident.login} is a ${ident.type} with no human signals and no machine ` +
    'naming, so this script will not guess. Somebody owns it; find out who ' +
    'before the question is urgent.'];
}

/** What remains tied to a human being, given a verdict. Pure. */
export function couplings(state) {
  return [...(COUPLINGS[state] ?? [])];
}

/** How many of these commits are attributed to a login. Pure. */
export function attributed(commits, login) {
  let total = 0;
  let mine = 0;
  let unlinked = 0;
  for (const commit of commits ?? []) {
    if (!commit || typeof commit !== 'object') continue;
    total += 1;
    const author = commit.author;
    if (!author || typeof author !== 'object' || !author.login) unlinked += 1;
    else if (String(author.login).toLowerCase() === String(login ?? '').toLowerCase()) mine += 1;
  }
  return { total, attributed: mine, unlinked };
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
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN to the credential your automation uses. ' +
      'An anonymous request has no identity to report');
    process.exitCode = 2;
    return;
  }
  const repo = process.argv[2] ?? null;
  const declared = ((process.env.GITHUB_MACHINE_LOGINS || "dummy-github-machine-logins") ?? '')
    .split(',').map((d) => d.trim()).filter(Boolean);

  const me = await get(token, '/user');
  const ident = me.status === 200 ? identity(me.body) : null;
  if (ident) console.log(`login=${ident.login} type=${ident.type}`);
  else console.log(`GET /user returned ${me.status} with no profile in it`);

  const signals = humanSignals(me.status === 200 ? me.body : null);
  const machine = ident ? machineShaped(ident.login, declared) : false;
  const [state, detail] = classify(ident, signals, machine);
  console.log(`${state}: ${detail}`);

  if (ident && state !== 'app-installation') {
    const orgs = await get(token, '/user/orgs');
    if (orgs.status === 200 && Array.isArray(orgs.body)) {
      const names = orgs.body.map((o) => o?.login).filter(Boolean);
      console.log(`this identity reaches ${names.length} organization(s) ` +
        `through one person's membership: ${names.join(', ') || 'none listed'}`);
    } else {
      console.log(`GET /user/orgs returned ${orgs.status}, so the ` +
        'organizations this identity borrows could not be listed');
    }
  }

  if (repo && ident) {
    const commits = await get(token, `/repos/${repo}/commits?per_page=100`);
    if (commits.status === 200) {
      const counts = attributed(commits.body, ident.login);
      console.log(`attribution: ${counts.attributed} of the last ` +
        `${counts.total} commits in ${repo} are attributed to ${ident.login} ` +
        `(${counts.unlinked} are linked to no account at all)`);
    } else {
      console.log(`GET commits for ${repo} returned ${commits.status}`);
    }
  }

  for (const line of couplings(state)) console.log(`coupled: ${line}`);

  if (['personal-account', 'mixed-signals', 'unclassified-user'].includes(state)) {
    console.log('repair: install a GitHub App owned by the organization and ' +
      'run the automation as its installation. The identity becomes ' +
      'my-app[bot] and no leaver process touches it.');
    console.log('repair: if an App is genuinely not possible, create a ' +
      "dedicated machine account, document its owner, and put its credentials " +
      "in the team's secret manager rather than on one person's laptop.");
  }

  console.log(JSON.stringify({
    login: ident?.login ?? null,
    type: ident?.type ?? null,
    human_signals: signals,
    machine_shaped: machine,
    state,
  }, null, 2));
  process.exitCode = (state === 'personal-account' || state === 'mixed-signals') ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main() and set an exit code that fails a passing suite.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
