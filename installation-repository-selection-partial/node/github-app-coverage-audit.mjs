/**
 * Report how much of an organization a GitHub App installation can actually see.
 *
 * Read only. Two GET requests and no writes. The repair is printed, never
 * performed.
 */
const API = 'https://api.github.com';
const UA = 'github-app-coverage-audit/1.0';

/**
 * Repositories the organization actually has, or null if it cannot be known.
 * total_private_repos is only returned to callers with enough access; without it
 * the public count is a floor, and a coverage figure built on a floor understates
 * the gap, so this returns null rather than a number.
 */
export function expectedTotal(org) {
  if (!org || typeof org !== 'object') return null;
  const pub = org.public_repos;
  const priv = org.total_private_repos;
  if (pub === null || pub === undefined) return null;
  if (priv === null || priv === undefined) return null;
  return Number(pub) + Number(priv);
}

/**
 * Compare what the installation sees against what exists. Pure.
 * Returns [state, detail].
 */
export function coverage(selection, seen, expected) {
  const sel = String(selection ?? '').trim().toLowerCase();

  if (sel === 'all') {
    return ['all-repositories',
      `${seen} repository(ies) visible, and repository_selection is 'all', so ` +
      'repositories created later join the installation automatically.'];
  }

  if (sel !== 'selected') {
    return ['unknown-selection',
      `repository_selection is ${JSON.stringify(selection)}, which is neither ` +
      "'all' nor 'selected'. Do not assume coverage from a value you cannot " +
      'interpret.'];
  }

  if (expected === null || expected === undefined) {
    return ['unmeasured',
      `${seen} repository(ies) selected. The organization's own total is not ` +
      'readable with this credential, so this is a count and not a coverage ' +
      'figure. Say so in the report rather than implying completeness.'];
  }

  if (seen > expected) {
    return ['inconsistent',
      `${seen} repository(ies) visible against an organization total of ` +
      `${expected}. The installation spans more than this organization, or one ` +
      'of the two counts is stale. Resolve it before quoting either.'];
  }

  if (seen === expected) {
    return ['selected-complete',
      `${seen} of ${expected} today, and nothing keeps it that way: a ` +
      "'selected' installation does not pick up repositories created later, so " +
      'this is complete by coincidence.'];
  }

  const pct = Math.round((100 * seen) / expected);
  return ['partial',
    `${seen} of ${expected} repositories. Every endpoint answers truthfully ` +
    `about those ${seen} and says nothing at all about the other ` +
    `${expected - seen}, so a clean report here covers ${pct}% of the ` +
    'organization.'];
}

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
}

async function get(token, url, params = {}) {
  const u = new URL(url);
  for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
  return fetch(u, { headers: headers(token) });
}

export async function installationView(token, api = API) {
  const names = [];
  let selection = null;
  let total = 0;
  let page = 1;
  for (;;) {
    const res = await get(token, `${api}/installation/repositories`,
                          { per_page: 100, page });
    if (res.status !== 200) {
      throw new Error(`${res.status} from GET /installation/repositories: this ` +
                      'needs an App installation token');
    }
    const body = await res.json();
    if (page === 1) {
      selection = body.repository_selection;
      total = Number(body.total_count ?? 0);
    }
    const items = body.repositories ?? [];
    names.push(...items.map((r) => String(r.full_name ?? '')));
    if (items.length < 100) break;
    page += 1;
  }
  return { selection, total, names };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN (an App installation token, read-only)');
    process.exitCode = 2;
    return;
  }
  const at = process.argv.indexOf('--org');
  const org = at >= 0 ? process.argv[at + 1] : null;
  if (!org) {
    console.error('pass --org <login>');
    process.exitCode = 2;
    return;
  }

  const { selection, total: seen } = await installationView(token);

  const orgRes = await get(token, `${API}/orgs/${org}`);
  const expected = orgRes.status === 200 ? expectedTotal(await orgRes.json()) : null;

  const [state, detail] = coverage(selection, seen, expected);
  const line = `${state.padEnd(18)} ${detail}`;
  if (state === 'all-repositories') {
    console.log(line);
    return;
  }

  console.warn(line);
  console.warn('  repair: switch the installation to All repositories, or add the ' +
               'missing repositories to it. Then have the tool print its own ' +
               'coverage next to its findings, so a clean report can never again ' +
               'appear without the number of repositories behind it.');
  process.exitCode = 1;
}

// Only run when invoked directly. The test file imports this module, and without
// the guard main() would run there too, fail on the missing token, and set a
// non-zero exit code that fails the whole test file even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
