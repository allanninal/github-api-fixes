/**
 * Say whether a 403 came from an organization IP allow list.
 *
 * Read only. Two GETs, plus one optional GraphQL query, which is a read that
 * happens to travel over the same verb a write would. Nothing is added to any
 * allow list: the script compares what is there against the address GitHub
 * saw and prints the request for an organization owner to make.
 *
 * The refusal is a check on the source address, not on the credential, which
 * is why the identical token succeeds from a laptop and fails on a runner.
 *
 * Environment:
 *   GITHUB_TOKEN       the same read-only token the failing job holds
 *   GITHUB_ORG         the organization the calls are refused by
 *   GITHUB_PROBE_PATH  optional org-scoped path to probe
 *   GITHUB_EGRESS      optional comma-separated CIDRs you believe you use
 *   GITHUB_ELSEWHERE   optional status the same token gets from a good machine
 *   GITHUB_READ_LIST   set to 1 to read the list itself (needs admin:org)
 */
const API = 'https://api.github.com';
const UA = 'github-ip-allow-list/1.0';

/** Sentences the other causes of a 403 put in the body. Corroboration only. */
export const QUOTA_MARKERS = ['api rate limit exceeded'];
export const SECONDARY_MARKERS = ['secondary rate limit'];
export const USER_AGENT_MARKERS = ['user-agent', 'user agent'];
export const ALLOW_LIST_MARKERS = ['ip allow list', 'not permitted to access this resource'];

/** One query, one point, refused before it is sent if it stops being a read. */
export const ALLOW_LIST_QUERY = `
query($login: String!) {
  organization(login: $login) {
    ipAllowListEnabledSetting
    ipAllowListForInstalledAppsEnabledSetting
    ipAllowListEntries(first: 100) {
      nodes { allowListValue isActive name }
    }
  }
}
`;

export const TOKEN_PREFIXES = [
  ['github_pat_', 'fine-grained PAT'],
  ['ghp_', 'classic PAT'],
  ['gho_', 'OAuth user token'],
  ['ghu_', 'App user-to-server token'],
  ['ghs_', 'App installation token'],
  ['ghr_', 'App refresh token'],
];

export const APP_MANAGED_APPLIES = ['App installation token'];

/** [REST requests, GraphQL points] this run will spend. Pure. */
export function readCost(withAllowList = false) {
  return [2, withAllowList ? 1 : 0];
}

/** Name the credential from its prefix. Pure. */
export function tokenKind(token) {
  const value = String(token ?? '').trim();
  for (const [prefix, name] of TOKEN_PREFIXES) {
    if (value.startsWith(prefix)) return name;
  }
  return 'unknown';
}

/** Which allow list judges this credential. Pure. [which, detail]. */
export function listThatApplies(kind) {
  if (APP_MANAGED_APPLIES.includes(kind)) {
    return ['org-list-plus-app-managed', "an installation token is judged "
      + "against the organization's list, and where the organization has "
      + "enabled the App-managed setting the App's own ranges are contributed "
      + 'to it automatically.'];
  }
  if (kind === 'App user-to-server token') {
    return ['org-list-only', 'a user-to-server token acts for a person, so it '
      + "is judged against the organization's own list even when the App's "
      + 'ranges are allowed. An App whose background sync works and whose '
      + 'interactive calls do not is this exact case.'];
  }
  return ['org-list-only', 'this credential carries no App identity, so only '
    + "the organization's own allow list applies to it."];
}

/** Four dot-separated numbers in 0..255. Pure. No regular expression. */
export function looksLikeIpv4(text) {
  const parts = String(text ?? '').split('.');
  if (parts.length !== 4) return false;
  for (const part of parts) {
    if (part.length === 0 || part.length > 3) return false;
    for (const ch of part) if (ch < '0' || ch > '9') return false;
    if (Number(part) > 255) return false;
  }
  return true;
}

/** A rough IPv6 test. The script never does arithmetic on one. Pure. */
export function looksLikeIpv6(text) {
  const value = String(text ?? '');
  if ((value.match(/:/g) || []).length < 2) return false;
  for (const group of value.split(':')) {
    if (group === '') continue;
    if (group.length > 4) return false;
    for (const ch of group.toLowerCase()) {
      if (!'0123456789abcdef'.includes(ch)) return false;
    }
  }
  return true;
}

/** The address GitHub says it saw, or null. Pure. Tokenised, not matched. */
export function addressInMessage(message) {
  for (const raw of String(message ?? '').split(/\s+/)) {
    let candidate = raw;
    while (candidate.length && '.,;:()[]<>"\''.includes(candidate[candidate.length - 1])) {
      candidate = candidate.slice(0, -1);
    }
    while (candidate.length && '.,;:()[]<>"\''.includes(candidate[0])) {
      candidate = candidate.slice(1);
    }
    if (looksLikeIpv4(candidate) || looksLikeIpv6(candidate)) return candidate;
  }
  return null;
}

/** Case-insensitive header read against a plain object. Pure. */
export function headerValue(headers, name) {
  if (!headers || typeof headers !== 'object') return null;
  const wanted = String(name).toLowerCase();
  for (const key of Object.keys(headers)) {
    if (key.toLowerCase() === wanted) return headers[key];
  }
  return null;
}

/** Sort one refusal into its cause. Pure. [state, detail]. */
export function classifyRefusal(status, bodyText, headers = null) {
  const text = String(bodyText ?? '').toLowerCase();
  const code = Number(status) || 0;
  if (![401, 403, 429].includes(code)) {
    return ['not-a-refusal', `HTTP ${status} is not a refusal, so there is `
      + 'nothing here to sort.'];
  }
  if (SECONDARY_MARKERS.some((m) => text.includes(m))) {
    return ['secondary-limit', 'the body names a secondary rate limit. Wait '
      + 'for retry-after and slow down; no allow-list entry is involved.'];
  }
  const remaining = headerValue(headers, 'x-ratelimit-remaining');
  if (QUOTA_MARKERS.some((m) => text.includes(m)) || String(remaining) === '0') {
    return ['primary-quota-exhausted', 'primary quota is spent. '
      + 'x-ratelimit-reset says when it returns, and the address is not the '
      + 'problem.'];
  }
  if (addressInMessage(bodyText) !== null) {
    return ['ip-allow-list', 'the body names an IP address, which no other 403 '
      + 'on this API does. This is a check on where the request came from, not '
      + 'on what it carried.'];
  }
  if (ALLOW_LIST_MARKERS.some((m) => text.includes(m))) {
    return ['ip-allow-list-unaddressed', 'the body reads like an allow-list '
      + 'refusal but names no address. Treat the cause as the allow list and '
      + 'get the egress address another way.'];
  }
  if (USER_AGENT_MARKERS.some((m) => text.includes(m))) {
    return ['user-agent-rule', 'the body names the User-Agent header. That '
      + 'check runs before authentication and has its own note.'];
  }
  if (code === 401) {
    return ['credential-rejected', '401 means the credential itself was not '
      + 'accepted. An allow list refuses with 403 and a body that names an '
      + 'address.'];
  }
  return ['permission-or-role', 'no rule named itself in the body, which is '
    + 'what a missing permission or too low a repository role looks like.'];
}

/** Dotted quad to a number, or null. Pure. */
export function ipv4ToInt(text) {
  if (!looksLikeIpv4(text)) return null;
  let total = 0;
  for (const part of String(text).split('.')) total = total * 256 + Number(part);
  return total;
}

/** Is this address inside this CIDR. Pure. true, false or null for unevaluated. */
export function cidrContains(cidr, address) {
  const value = String(cidr ?? '').trim();
  if (!value) return null;
  let net = value;
  let prefix = 32;
  if (value.includes('/')) {
    const cut = value.indexOf('/');
    net = value.slice(0, cut);
    const bits = value.slice(cut + 1);
    if (!bits.length || [...bits].some((c) => c < '0' || c > '9')) return null;
    prefix = Number(bits);
  }
  if (net.includes(':') || String(address ?? '').includes(':')) return null;
  const left = ipv4ToInt(net);
  const right = ipv4ToInt(address);
  if (left === null || right === null || prefix > 32) return null;
  if (prefix === 0) return true;
  const mask = prefix === 32 ? 0xFFFFFFFF : (0xFFFFFFFF - (2 ** (32 - prefix) - 1));
  return (left & mask) >>> 0 === (right & mask) >>> 0;
}

/** Does any active entry cover this address. Pure. [state, entry]. */
export function coveredBy(entries, address) {
  if (address === null || address === undefined) return ['address-unknown', null];
  if (!entries || entries.length === 0) return ['no-entries', null];
  let inactive = null;
  let unevaluated = false;
  for (const entry of entries) {
    const hit = cidrContains(entry.value, address);
    if (hit === null) { unevaluated = true; continue; }
    if (hit && entry.active) return ['covered', entry];
    if (hit && !inactive) inactive = entry;
  }
  if (inactive) return ['covered-but-inactive', inactive];
  if (unevaluated) return ['not-covered-some-unevaluated', null];
  return ['not-covered', null];
}

/** Declared egress against the address GitHub really saw. Pure. */
export function egressAssumption(declared, address) {
  if (address === null || address === undefined) {
    return ['address-unknown', 'no address was reported, so there is nothing '
      + 'to compare your declared egress against.'];
  }
  if (!declared || declared.length === 0) {
    return ['nothing-declared', 'declare the ranges you believe this job '
      + 'leaves from and this becomes a check rather than a reading.'];
  }
  for (const cidr of declared) {
    if (cidrContains(cidr, address) === true) {
      return ['egress-as-expected', `the address GitHub saw is inside ${cidr}, `
        + 'so your egress assumption holds and the range simply is not allowed '
        + 'yet.'];
    }
  }
  return ['egress-assumption-wrong', 'the address GitHub saw is outside every '
    + `range you declared (${declared.join(', ')}), so adding those ranges `
    + 'would not have helped. Find out what this job really egresses through '
    + 'before asking for a change.'];
}

/** Two readings of the same call from two machines. Pure. [state, detail]. */
export function pairedReading(statusHere, statusElsewhere) {
  const here = Number(statusHere) || 0;
  const there = (statusElsewhere === null || statusElsewhere === undefined)
    ? null : Number(statusElsewhere);
  if (there === null) {
    return ['single-reading', 'only this machine was read. Supply the status '
      + 'the same token gets from a machine that works and the network path '
      + 'stops being a hunch.'];
  }
  if (here === 403 && there === 200) {
    return ['network-path', 'the same token is refused here and accepted '
      + 'there, so the difference is the source address and nothing else.'];
  }
  if (here === 403 && there === 403) {
    return ['refused-everywhere', 'both addresses are refused. Either the '
      + 'allow list covers neither, or the cause is the credential after all.'];
  }
  if (here === 200) {
    return ['no-refusal', 'this machine was not refused, so there is nothing '
      + 'to explain from here. Run this on the machine that fails.'];
  }
  return ['inconclusive', 'the pair of statuses does not describe an allow '
    + 'list. Sort the refusal by its body first.'];
}

/** Bare words in a GraphQL document. Pure. */
export function words(document) {
  const out = [];
  let current = '';
  for (const ch of String(document ?? '')) {
    if (/[A-Za-z0-9_]/.test(ch)) current += ch;
    else { if (current) out.push(current.toLowerCase()); current = ''; }
  }
  if (current) out.push(current.toLowerCase());
  return out;
}

/** Would this document change something. Pure. */
export function refusesMutation(document) {
  const banned = ['mutation', 'subscription'];
  return words(document).some((w) => banned.includes(w));
}

/** Normalise the GraphQL answer. Pure. [setting, appsSetting, entries, note]. */
export function allowListFromGraphql(body) {
  if (!body || typeof body !== 'object') {
    return [null, null, [], 'no readable GraphQL body came back.'];
  }
  const errors = body.errors || [];
  const org = (body.data && body.data.organization) || null;
  if (!org) {
    const detail = errors.length && errors[0] && errors[0].message
      ? errors[0].message : 'no organization in the response';
    return [null, null, [], `the organization block was not returned: ${detail}. `
      + 'Reading an IP allow list needs admin:org-class access, so an '
      + 'unreadable list here means your token, not an empty list.'];
  }
  const nodes = (org.ipAllowListEntries && org.ipAllowListEntries.nodes) || [];
  const entries = nodes.filter((n) => n && typeof n === 'object').map((n) => ({
    value: n.allowListValue,
    active: Boolean(n.isActive),
    name: n.name || '',
  }));
  return [org.ipAllowListEnabledSetting,
    org.ipAllowListForInstalledAppsEnabledSetting,
    entries,
    `read ${entries.length} entries.`];
}

/** The finding, in one state. Pure. [state, detail]. */
export function verdict(refusalState, coverageState, setting) {
  if (!['ip-allow-list', 'ip-allow-list-unaddressed'].includes(refusalState)) {
    return [refusalState, 'this refusal is not an allow-list refusal, so the '
      + 'rest of this script is not about your problem.'];
  }
  if (String(setting ?? '').toUpperCase() === 'DISABLED') {
    return ['allow-list-disabled', 'the organization reports the allow list as '
      + 'disabled, which does not agree with the refusal. Check that you read '
      + 'the same organization the failing call was made against.'];
  }
  if (coverageState === 'covered-but-inactive') {
    return ['entry-exists-but-is-off', 'an entry covering this address exists '
      + 'and is switched off. Somebody already did the work; it just is not '
      + 'active.'];
  }
  if (coverageState === 'covered') {
    return ['covered-yet-refused', 'an active entry covers this address, so '
      + 'either the refusal predates the entry or the call was against a '
      + 'different organization. Re-run the probe before escalating.'];
  }
  if (['not-covered', 'not-covered-some-unevaluated'].includes(coverageState)) {
    return ['address-not-covered', 'no active entry covers the address GitHub '
      + 'saw. This is the ordinary case and the repair is one entry.'];
  }
  return ['rule-unreadable', 'the refusal is an allow-list refusal and the list '
    + 'itself could not be read, which needs admin:org-class access. The cause '
    + 'is established; the entry that would have covered you is not.'];
}

/** The sentence a reader has to act on. Pure. Nothing here is executed. */
export function repair(state, address, org) {
  if (state === 'entry-exists-but-is-off') {
    return `ask an owner of ${org} to switch the existing entry back on. Adding `
      + 'a second entry for the same range will not help while the first one is '
      + 'inactive.';
  }
  if (['address-not-covered', 'rule-unreadable'].includes(state)) {
    const what = address ? `${address}${address.includes('.') ? '/32' : ''}`
      : "this job's egress range";
    return `ask an owner of ${org} to add ${what}, or the documented egress `
      + 'range of this runner pool, to the organization IP allow list. For a '
      + 'GitHub App, enabling the App-managed allow list contributes its ranges '
      + 'for installation tokens. Nothing here adds anything.';
  }
  if (state === 'covered-yet-refused') {
    return 're-run the probe from this machine. A covered address that is still '
      + 'refused usually means the reading and the refusal came from different '
      + 'places or different organizations.';
  }
  if (state === 'allow-list-disabled') {
    return 'confirm which organization the failing call names. A disabled list '
      + 'cannot produce this refusal.';
  }
  return 'sort the refusal by its body before doing anything about addresses. '
    + 'This script found no allow-list refusal to repair.';
}

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'Content-Type': 'application/json',
    'User-Agent': UA,
  };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const org = (process.env.GITHUB_ORG || "dummy-github-org");
  if (!token || !org) {
    console.error('set GITHUB_TOKEN (read-only is enough) and GITHUB_ORG');
    process.exitCode = 2;
    return;
  }
  const readList = (process.env.GITHUB_READ_LIST || "dummy-github-read-list") === '1';
  const declared = ((process.env.GITHUB_EGRES || "dummy-github-egres")S || '').split(',')
    .map((s) => s.trim()).filter(Boolean);
  const elsewhere = (process.env.GITHUB_ELSEWHERE || "dummy-github-elsewhere")
    ? Number((process.env.GITHUB_ELSEWHERE || "dummy-github-elsewhere")) : null;
  const [rest, points] = readCost(readList);
  console.log(`read cost: ${rest} REST request(s) against the core hourly quota, `
    + `${points} GraphQL point(s)`);

  const kind = tokenKind(token);
  const [whichList, whichDetail] = listThatApplies(kind);
  console.log(`token: ${kind}. ${whichList}: ${whichDetail}`);

  const path = (process.env.GITHUB_PROBE_PAT || "dummy-github-probe-pat")H || `/orgs/${org}/repos?per_page=1`;
  const probe = await fetch(`${API}${path}`, { headers: headers(token) });
  const bodyText = await probe.text();
  console.log(`probe: GET ${path} -> HTTP ${probe.status}`);

  const headerBag = {};
  probe.headers.forEach((value, key) => { headerBag[key] = value; });
  const [refusalState, refusalDetail] = classifyRefusal(probe.status, bodyText, headerBag);
  console.log(`refusal: ${refusalState}. ${refusalDetail}`);

  const address = addressInMessage(bodyText);
  if (address) console.log(`address GitHub saw: ${address}`);
  const [egressState, egressDetail] = egressAssumption(declared, address);
  console.log(`${egressState}: ${egressDetail}`);

  let setting = null;
  let appsSetting = null;
  let entries = [];
  let coverageState = 'rule-unread';
  if (readList) {
    if (refusesMutation(ALLOW_LIST_QUERY)) {
      console.error('the allow-list document is not a read; refusing to send it');
      process.exitCode = 2;
      return;
    }
    const graph = await fetch(`${API}/graphql`, {
      // A GraphQL query is a read. This is only how the document travels, and
      // refusesMutation() has already rejected anything that is not a read.
      method: 'POST',
      headers: headers(token),
      body: JSON.stringify({ query: ALLOW_LIST_QUERY, variables: { login: org } }),
    });
    let payload = null;
    try { payload = await graph.json(); } catch { payload = null; }
    [setting, appsSetting, entries] = allowListFromGraphql(payload);
    [coverageState] = coveredBy(entries, address);
    console.log(`allow list: setting=${setting}, apps=${appsSetting}, `
      + `entries=${entries.length}, coverage=${coverageState}`);
  }

  const [pairedState, pairedDetail] = pairedReading(probe.status, elsewhere);
  console.log(`paired reading: ${pairedState}. ${pairedDetail}`);
  const [state, detail] = verdict(refusalState, coverageState, setting);
  console.log(`${state}: ${detail}`);
  console.log(`repair: ${repair(state, address, org)}`);

  console.log(JSON.stringify({
    organization: org,
    probe_path: path,
    probe_status: probe.status,
    token_kind: kind,
    list_that_applies: whichList,
    refusal_state: refusalState,
    address_github_saw: address,
    declared_egress: declared,
    egress_state: egressState,
    allow_list_setting: setting,
    entries_read: entries.length,
    coverage_state: coverageState,
    paired_state: pairedState,
    state,
    detail,
    repair: repair(state, address, org),
  }, null, 2));
  process.exitCode = ['address-not-covered', 'entry-exists-but-is-off',
    'rule-unreadable'].includes(state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
