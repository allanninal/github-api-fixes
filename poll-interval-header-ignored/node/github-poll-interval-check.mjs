/**
 * Compare a configured poll interval against the floor GitHub declares.
 *
 * Read only. One GET against an events endpoint, and the finding comes from its
 * response headers.
 *
 * Events endpoints return x-poll-interval: the minimum seconds to wait. The
 * feed is regenerated no faster than that, so a request underneath it returns
 * the page you already have.
 */
const API = 'https://api.github.com';
const UA = 'github-poll-interval-check/1.0';

// What the events endpoints have historically returned when nothing else says
// otherwise. Used only as a last resort, and labelled as an assumption.
export const DEFAULT_FLOOR = 60;

/** Seconds from a Cache-Control header, or null. Pure. */
export function parseMaxAge(value) {
  const match = /max-age\s*=\s*(\d+)/i.exec(String(value ?? ''));
  if (!match) return null;
  const seconds = Number.parseInt(match[1], 10);
  return Number.isFinite(seconds) && seconds > 0 ? seconds : null;
}

/**
 * The minimum poll interval the server declared. Pure.
 * Returns [seconds, source]. The source matters: "the server said 60" and
 * "nothing said anything so I assumed 60" are the same number with very
 * different confidence.
 */
export function floorSeconds(headers, fallback = DEFAULT_FLOOR) {
  const lowered = {};
  for (const [k, v] of Object.entries(headers ?? {})) lowered[String(k).toLowerCase()] = v;

  const declared = Number.parseInt(String(lowered['x-poll-interval'] ?? '').trim(), 10);
  if (Number.isFinite(declared) && declared > 0) return [declared, 'x-poll-interval'];

  const age = parseMaxAge(lowered['cache-control']);
  if (age) return [age, 'cache-control max-age'];
  return [fallback, 'documented default'];
}

/**
 * Compare the configured interval against the floor. Pure.
 * Both directions are findings; only one of them shows up on a quota graph.
 */
export function assess(configured, floor, hasEtag) {
  const every = Math.max(1, Number.parseInt(configured, 10) || 1);
  const min = Math.max(1, Number.parseInt(floor, 10) || 1);

  const polls = Math.round(3600 / every);
  const allowed = Math.round(3600 / min);
  const wasted = Math.max(0, polls - allowed);

  let state = 'at-floor';
  if (every < min) state = 'under-floor';
  else if (every > min * 1.5) state = 'over-floor';

  return {
    state,
    configured: every,
    floor: min,
    polls_per_hour: polls,
    allowed_per_hour: allowed,
    wasted_per_hour: wasted,
    billable_per_hour: hasEtag ? 0 : wasted,
    extra_staleness_s: Math.max(0, every - min),
  };
}

/** Turn the comparison into a finding. Pure. */
export function verdict(assessment) {
  const floor = assessment.floor ?? DEFAULT_FLOOR;
  const configured = assessment.configured ?? floor;

  if (assessment.state === 'under-floor') {
    if (assessment.billable_per_hour) {
      return ['burning-quota',
        `${assessment.billable_per_hour} request(s) an hour beyond the ${floor}s ` +
        'floor the server declared, and every one of them is billable because ' +
        'no etag is being sent. They return the page you already have.'];
    }
    return ['free-but-pointless',
      `${assessment.wasted_per_hour} conditional request(s) an hour beyond the ` +
      `${floor}s floor. They cost no quota, because an unchanged feed answers ` +
      '304, but they cannot return anything new either: the feed is not ' +
      'regenerated faster than that.'];
  }
  if (assessment.state === 'over-floor') {
    return ['slower-than-needed',
      `polling every ${configured}s against a ${floor}s floor adds up to ` +
      `${assessment.extra_staleness_s}s of avoidable staleness and saves ` +
      'nothing, because the requests you skipped would have been 304s.'];
  }
  return ['at-floor',
    `polling every ${configured}s against a floor of ${floor}s: nothing to ` +
    'reclaim in either direction.'];
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN (a read-only token is enough)');
    process.exitCode = 2;
    return;
  }
  const target = process.argv[2];
  if (!target) {
    console.error('usage: node github-poll-interval-check.mjs owner/name [interval]');
    process.exitCode = 2;
    return;
  }
  const interval = Number.parseInt(process.argv[3] ?? '5', 10) || 5;
  const path = target.includes('/') ? `/repos/${target}/events` : `/users/${target}/events`;

  const res = await fetch(API + path, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': UA,
    },
  });
  if (res.status !== 200) {
    console.error(`GET ${path} returned ${res.status}`);
    process.exitCode = 2;
    return;
  }

  const headers = {};
  for (const [k, v] of res.headers.entries()) headers[k.toLowerCase()] = v;
  const [floor, source] = floorSeconds(headers);
  const etag = headers.etag;
  const page = await res.json().catch(() => []);

  console.log(`${path}: floor ${floor}s (from ${source}), etag ` +
    `${etag ? 'present' : 'absent'}, ${Array.isArray(page) ? page.length : 0} event(s) on this page`);
  if (source !== 'x-poll-interval') {
    console.warn('x-poll-interval was not on the response, so the floor above ' +
      'is an assumption. Read it per response rather than hardcoding one: the ' +
      'value goes up when GitHub is busy.');
  }

  const result = assess(interval, floor, Boolean(etag));
  const [state, detail] = verdict(result);
  console.log(`${state}: ${detail}`);

  if (state !== 'at-floor') {
    console.log('repair: sleep for the value of x-poll-interval on the last ' +
      'response, re-reading it every cycle, and send the etag back as ' +
      'If-None-Match so an unchanged page is free.');
  }
  if (state === 'slower-than-needed') {
    console.log('repair: the events feed holds only a window of recent ' +
      'activity, so an interval far above the floor can miss events outright ' +
      'rather than merely notice them late.');
  }

  console.log(JSON.stringify({
    path, floor, floor_source: source, etag: Boolean(etag),
    assessment: result, state,
  }, null, 2));
  process.exitCode = (state === 'burning-quota' || state === 'slower-than-needed') ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main() and fail on the missing token.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
