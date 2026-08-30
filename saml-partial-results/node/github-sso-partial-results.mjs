/**
 * Find organizations that GitHub withheld from a 200 because of SAML SSO.
 *
 * Read only. GET requests and nothing else: read:org is enough. The repair is
 * printed, never performed.
 */
const API = 'https://api.github.com';
const UA = 'github-sso-partial-results/1.0';

/**
 * Parse an X-GitHub-SSO header value. Pure, so both forms are testable.
 *
 * On a 200:  partial-results; organizations=21955855,20582480
 * On a 403:  required; url=https://github.com/orgs/acme/sso?authorization_request=...
 *
 * Anything else is "unknown" rather than "none": a header nobody parsed is still
 * a header GitHub sent, and reading it as absence is how a partial answer becomes
 * a clean bill of health.
 */
export function parseSso(value) {
  const raw = String(value ?? '').trim();
  if (!raw) return { kind: 'none', organizations: [], url: null };

  const parts = raw.split(';').map((p) => p.trim()).filter(Boolean);
  let kind = (parts[0] ?? '').toLowerCase();
  let organizations = [];
  let url = null;
  for (const part of parts.slice(1)) {
    const at = part.indexOf('=');
    if (at < 0) continue;
    const name = part.slice(0, at).trim().toLowerCase();
    const val = part.slice(at + 1).trim();
    if (name === 'organizations') {
      organizations = val.split(',').map((o) => o.trim()).filter(Boolean);
    } else if (name === 'url') {
      url = val;
    }
  }
  if (kind !== 'partial-results' && kind !== 'required') kind = 'unknown';
  return { kind, organizations, url };
}

/**
 * Decide what one response means. Pure. Returns [state, detail]. The header
 * outranks the status code.
 */
export function verdict(status, sso, listed) {
  const kind = sso.kind;

  if (kind === 'partial-results') {
    const hidden = sso.organizations ?? [];
    return ['partial',
      `${listed} organization(s) in the body and ${hidden.length} withheld ` +
      `(${hidden.join(', ') || 'unnamed'}). The status is 200 and the JSON is ` +
      'valid; the answer is not.'];
  }

  if (kind === 'required') {
    return ['authorization-required',
      'the token is not SSO-authorized and GitHub said so out loud. Authorize ' +
      `it at ${sso.url ?? "the org's SSO page"}`];
  }

  if (kind === 'unknown') {
    return ['unreadable',
      'an X-GitHub-SSO header was sent and this parser did not understand it. ' +
      'Treat that as partial, never as clean, and read the raw value before ' +
      'trusting the list.'];
  }

  if (status === 403) {
    return ['forbidden',
      '403 with no X-GitHub-SSO header, so this is not SSO. Look at org OAuth ' +
      'app restrictions, an IP allow list, or a missing read:org scope instead.'];
  }
  if (status !== 200) return ['unexpected', `HTTP ${status}`];

  return ['complete', `${listed} organization(s), no partial-results header`];
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

export async function listOrgs(token, api = API) {
  const orgs = [];
  let finding = { status: 200, sso: { kind: 'none', organizations: [], url: null } };
  let page = 1;
  for (;;) {
    const res = await get(token, `${api}/user/orgs`, { per_page: 100, page });
    const sso = parseSso(res.headers.get('x-github-sso'));
    if (sso.kind !== 'none' && finding.sso.kind === 'none') {
      finding = { status: res.status, sso };
    }
    if (res.status !== 200) { finding.status = res.status; break; }
    const items = await res.json();
    orgs.push(...items);
    if (items.length < 100) break;
    page += 1;
  }
  return { orgs, finding };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN (read:org is enough)');
    process.exitCode = 2;
    return;
  }

  const { orgs, finding } = await listOrgs(token);
  const [state, detail] = verdict(finding.status, finding.sso, orgs.length);

  if (state === 'complete') {
    console.log(`${state.padEnd(22)} ${detail}`);
    return;
  }

  console.warn(`${state.padEnd(22)} ${detail}`);
  console.warn(`  visible: ${orgs.map((o) => o.login).join(', ') || 'none'}`);

  if (process.argv.includes('--resolve-ids') &&
      finding.sso.kind === 'partial-results') {
    for (const id of finding.sso.organizations) {
      const res = await get(token, `${API}/organizations/${id}`);
      const name = res.status === 200 ? (await res.json()).login : null;
      console.warn(`  withheld: ${id} ` +
        `(${name ?? 'could not be resolved with this token either'})`);
    }
  }

  console.warn('  repair: authorize this token for the withheld organizations in ' +
               'your GitHub settings under SSO, or run one credential per ' +
               'organization and stop asking a single token a question it cannot ' +
               'answer completely.');
  process.exitCode = 1;
}

// Only run when invoked directly. The test file imports this module, and without
// the guard main() would run there too, fail on the missing token, and set a
// non-zero exit code that fails the whole test file even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
