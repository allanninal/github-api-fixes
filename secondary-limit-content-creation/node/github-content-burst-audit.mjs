/**
 * Find bursts of created issues and comments that will trip the content limit.
 *
 * Read only. Every request is a GET, and the repair is printed rather than run.
 *
 * Content-generating requests are capped at about 80 a minute and 500 an hour,
 * separately from the hourly quota, and no API reports the remaining allowance.
 */
const API = 'https://api.github.com';
const UA = 'github-content-burst-audit/1.0';

export const MINUTE_LIMIT = 80;
export const HOUR_LIMIT = 500;

// A burst whose newest item is inside this many seconds of now is still running.
const LIVE_SECONDS = 900;

/** ISO 8601 to epoch seconds, or null. Pure. */
export function parseTs(value) {
  const text = String(value ?? '').trim();
  if (!text) return null;
  const ms = Date.parse(text);
  return Number.isFinite(ms) ? ms / 1000 : null;
}

/**
 * Most timestamps falling inside any window of that many seconds. Pure.
 * Two pointers over a sorted list. Returns [count, endingAt].
 */
export function peakRate(times, window) {
  const values = (times ?? []).filter((t) => t !== null && t !== undefined)
    .map(Number).sort((a, b) => a - b);
  let peak = 0;
  let at = null;
  let start = 0;
  for (let end = 0; end < values.length; end += 1) {
    while (values[end] - values[start] >= window) start += 1;
    const count = end - start + 1;
    if (count > peak) { peak = count; at = values[end]; }
  }
  return [peak, at];
}

/**
 * Group created_at timestamps by the login that created them. Pure.
 * The limit is per account, so a repository-wide count is the wrong number.
 */
export function byActor(items) {
  const out = {};
  for (const item of items ?? []) {
    const user = item.user ?? {};
    const login = String(user.login ?? 'unknown');
    const when = parseTs(item.created_at);
    if (when === null) continue;
    const bucket = (out[login] ??= { times: [], type: user.type ?? 'User' });
    bucket.times.push(when);
  }
  return out;
}

/**
 * Classify one account's creation pattern. Pure. Returns [state, detail].
 * now is a parameter so the same input always produces the same output.
 */
export function verdict(peakMinute, peakHour, lastSeen, now) {
  if (!peakMinute) return ['quiet', 'nothing created in the window that was read'];

  const age = lastSeen === null || lastSeen === undefined
    ? null : Math.max(0, Number(now) - Number(lastSeen));
  const when = age === null ? 'at an unknown time'
    : age < LIVE_SECONDS ? 'still running' : 'already finished';
  const tail = `, ${when} (newest item ${Math.floor((age ?? 0) / 60)} minute(s) ago)`;

  if (peakMinute >= MINUTE_LIMIT) {
    return ['over-minute',
      `${peakMinute} created inside one minute against a ceiling of ${MINUTE_LIMIT}. ` +
      `This account has already been throttled or is about to be${tail}`];
  }
  if (peakHour >= HOUR_LIMIT) {
    return ['over-hour',
      `${peakHour} created inside one hour against a ceiling of ${HOUR_LIMIT}. ` +
      `Pacing under the per-minute limit is not enough on its own${tail}`];
  }
  if (peakMinute >= MINUTE_LIMIT * 0.8) {
    return ['near-minute',
      `${peakMinute} in a minute, ${Math.floor(100 * peakMinute / MINUTE_LIMIT)}% of ` +
      `the ceiling. One issue billed as two requests puts this over${tail}`];
  }
  if (peakHour >= HOUR_LIMIT * 0.8) {
    return ['near-hour',
      `${peakHour} in an hour, ${Math.floor(100 * peakHour / HOUR_LIMIT)}% of the ` +
      `ceiling. The per-minute rate is fine and the sustained rate is not${tail}`];
  }
  return ['clear',
    `densest minute ${peakMinute}, densest hour ${peakHour}, both well under ` +
    `${MINUTE_LIMIT} and ${HOUR_LIMIT}`];
}

function nextLink(res) {
  for (const part of (res.headers.get('link') ?? '').split(',')) {
    const chunk = part.trim();
    if (chunk.startsWith('<') && chunk.endsWith('rel="next"')) {
      return chunk.slice(1, chunk.indexOf('>'));
    }
  }
  return null;
}

async function get(token, url) {
  const res = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': UA,
    },
  });
  if (res.status === 401) {
    throw new Error('401 from GitHub: GITHUB_TOKEN is missing, expired or malformed');
  }
  if (res.status === 403 || res.status === 404) {
    throw new Error(`${res.status} from ${url}: this needs read access to the ` +
      "repository's issues. GitHub answers 404 rather than 403 when a token " +
      'cannot see a resource at all.');
  }
  if (!res.ok) throw new Error(`${res.status} from ${url}`);
  return res;
}

async function page(token, url, limit) {
  const out = [];
  let next = url;
  while (next && out.length < limit) {
    const res = await get(token, next);
    out.push(...(await res.json()));
    next = nextLink(res);
  }
  return out.slice(0, limit);
}

async function main() {
  const repo = process.argv[2];
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN (a read-only token is enough)');
    process.exitCode = 2;
    return;
  }
  if (!repo || !repo.includes('/')) {
    console.error('usage: node github-content-burst-audit.mjs owner/name');
    process.exitCode = 2;
    return;
  }

  const base = `${API}/repos/${repo}`;
  const limit = 600;
  const items = [
    ...await page(token,
      `${base}/issues?state=all&sort=created&direction=desc&per_page=100`, limit),
    ...await page(token,
      `${base}/issues/comments?sort=created&direction=desc&per_page=100`, limit),
  ];
  console.log(`read ${items.length} issue(s), pull request(s) and comment(s) on ${repo}`);

  const now = Date.now() / 1000;
  const actors = byActor(items);
  let findings = 0;
  const ranked = Object.entries(actors).sort((a, b) => b[1].times.length - a[1].times.length);
  for (const [login, bucket] of ranked) {
    const [peakMinute, minuteAt] = peakRate(bucket.times, 60);
    const [peakHour] = peakRate(bucket.times, 3600);
    const lastSeen = bucket.times.length ? Math.max(...bucket.times) : null;
    const [state, detail] = verdict(peakMinute, peakHour, lastSeen, now);
    const line = `${login} (${bucket.type}): ${detail}`;
    if (state === 'clear' || state === 'quiet') { console.log(line); continue; }
    findings += 1;
    console.warn(line);
    if (minuteAt) {
      console.warn(`  densest minute ended at ${new Date(minuteAt * 1000).toISOString()}`);
    }
    console.warn('  repair: pace this writer to one creating request per second ' +
      'and under 300 an hour, sleeping between items rather than relying on the ' +
      'network being slow.');
    console.warn('  repair: on a 403 carrying retry-after, pause every worker ' +
      'for that many seconds instead of retrying the one item, and checkpoint ' +
      'what was created so a resume does not duplicate it.');
  }

  console.log(`${ranked.length} author(s) examined, ${findings} over or near a ` +
    'content-creation ceiling');
  process.exitCode = findings ? 1 : 0;
}

// Only run when invoked directly. The test file imports this module, and without
// the guard main() would run there too, fail on the missing token, and set a
// non-zero exit code that fails the whole test file even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
