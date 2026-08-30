/**
 * Check the X-GitHub-Api-Version your client pins against the versions GitHub serves.
 *
 * Read only, and mostly unauthenticated: GET /versions needs no credential.
 * Nothing here changes a header, a deployment or a pin; the repair is printed.
 *
 * The state worth alerting on is "supported but behind", which is this problem
 * with months of notice attached.
 */
const API = 'https://api.github.com';
const UA = 'github-api-version-pin/1.0';

/** The version a request gets when it sends no header. Not unversioned. */
export const SERVER_DEFAULT = '2022-11-28';

const DATE = /^\d{4}-\d{2}-\d{2}$/;

/** What a refusal about the version looks like in prose, not as a number. */
const VERSION_WORDS = /api version|x-github-api-version|version.*(not supported|no longer)/i;

/** Whether a string is shaped like an API version. Pure. */
export function isVersion(value) {
  const text = String(value ?? '').trim();
  if (!DATE.test(text)) return false;
  const [year, month, day] = text.split('-').map(Number);
  return year >= 2000 && year <= 2999 && month >= 1 && month <= 12 &&
    day >= 1 && day <= 31;
}

/** Parse the GET /versions body into a sorted list of versions. Pure. */
export function supported(body) {
  if (!Array.isArray(body)) return [];
  return [...new Set(body.map((v) => String(v).trim()).filter(isVersion))].sort();
}

/** Supported versions strictly newer than the pin. Pure. */
export function behind(pin, versions) {
  return (versions ?? []).filter((v) => v > String(pin ?? ''));
}

/** The supported version closest to a pin, for the typo case. Pure. */
export function nearest(pin, versions) {
  if (!versions || !versions.length) return null;
  const digits = (v) => Number(String(v ?? '').replace(/\D/g, '') || '0');
  const target = digits(pin);
  return [...versions].sort((a, b) => (Math.abs(digits(a) - target) -
    Math.abs(digits(b) - target)) || a.localeCompare(b))[0];
}

/** Sort a pinned value into one of six states. Pure. */
export function classify(pin, versions) {
  if (!versions || !versions.length) {
    return ['no-versions-list',
      'GET /versions returned nothing version-shaped, so the pin cannot be ' +
      'judged. That is a failure of the check rather than a finding about the pin.'];
  }

  const newest = versions[versions.length - 1];
  if (pin === null || pin === undefined || !String(pin).trim()) {
    let detail = 'no X-GitHub-Api-Version header is sent, so requests get ' +
      `GitHub's default of ${SERVER_DEFAULT}. That is a real version with a ` +
      'real lifetime: unpinned means pinned by the server, and it moves ' +
      'without asking.';
    if (!versions.includes(SERVER_DEFAULT)) {
      detail += ' The default this script knows about is not on the served ' +
        'list any more, so check what the current one is.';
    }
    return ['unpinned', detail];
  }

  const value = String(pin).trim();
  if (!isVersion(value)) {
    return ['malformed-pin',
      `'${value}' is not shaped like an API version. It was never valid, so ` +
      'this is a typo rather than a retirement; the closest served version ' +
      `is ${nearest(value, versions)}.`];
  }
  if (versions.includes(value)) {
    const newer = behind(value, versions);
    if (!newer.length) {
      return ['supported-current', `${value} is the newest version GitHub serves.`];
    }
    return ['supported-behind',
      `${value} is still served, and ${newer.length} newer version(s) exist: ` +
      `${newer.join(', ')}. This is the state to alert on, because it is this ` +
      'problem with notice attached.'];
  }
  if (value < versions[0]) {
    return ['retired',
      `${value} is older than every supported version. Requests pinned to it ` +
      `are refused, and the oldest one still served is ${versions[0]}.`];
  }
  if (value > newest) {
    return ['not-yet-supported',
      `${value} is newer than every supported version, so it names a version ` +
      'that does not exist yet. Almost always a typo; the closest served ' +
      `version is ${nearest(value, versions)}.`];
  }
  return ['unknown-version',
    `${value} is a valid date and was never a published version. The closest ` +
    `served version is ${nearest(value, versions)}.`];
}

/** Whether a live response blames the API version. Pure. */
export function confirmsVersionRefusal(status, message) {
  if (status === null || status === undefined || status < 400) return false;
  return VERSION_WORDS.test(String(message ?? ''));
}

async function get(path, headers) {
  const res = await fetch(API + path, { headers });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return { status: res.status, body };
}

async function main() {
  const pinned = process.argv[2] ?? (process.env.GITHUB_API_VERSION || "dummy-github-api-version") ?? null;
  const path = process.argv[3] ?? '/meta';
  const headers = {
    Accept: 'application/vnd.github+json',
    'User-Agent': UA,
  };
  // Optional: a token only raises the rate limit this public check shares
  // with every other anonymous caller on the address.
  if ((process.env.GITHUB_TOKEN || "dummy-github-token")) {
    headers.Authorization = `Bearer ${(process.env.GITHUB_TOKEN || "dummy-github-token")}`;
  }

  const list = await get('/versions', headers);
  if (list.status !== 200) {
    console.error(`GET /versions returned ${list.status}, so there is no list ` +
      'to compare against');
    process.exitCode = 2;
    return;
  }
  const versions = supported(list.body);
  console.log(`supported: ${versions.join(', ') || 'nothing version-shaped'}`);

  const [state, detail] = classify(pinned, versions);
  console.log(`${state}: ${detail}`);

  if (pinned) {
    const live = await get(path, { ...headers, 'X-GitHub-Api-Version': String(pinned) });
    const message = live.body && typeof live.body === 'object' ? live.body.message : null;
    console.log(`${path} with the pin returned ${live.status}`);
    if (confirmsVersionRefusal(live.status, message)) {
      console.log('confirmed live: the response blames the version');
    } else if (live.status >= 400) {
      console.log(`the ${live.status} does not mention the version, so it has ` +
        'another cause');
    }
  }

  const broken = ['retired', 'unknown-version', 'not-yet-supported', 'malformed-pin'];
  if (broken.includes(state)) {
    const inBetween = pinned ? Math.max(behind(pinned, versions).length - 1, 0) : 0;
    console.log(`repair: move the pin to ${versions[versions.length - 1]}, ` +
      `reading the notes for ${inBetween} version(s) in between first`);
  }
  if (state === 'supported-behind') {
    console.log(`repair: schedule the move to ${versions[versions.length - 1]}; ` +
      'nothing is failing yet, which is the only good time to do it');
  }

  console.log(JSON.stringify({
    pinned,
    supported: versions,
    behind_by: behind(pinned ?? '', versions).length,
    state,
  }, null, 2));
  process.exitCode = broken.includes(state) ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main() and set an exit code that fails a passing suite.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
