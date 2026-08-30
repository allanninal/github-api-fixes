/**
 * Read how long each GitHub credential has left, before it costs you an outage.
 *
 * Read only. One GET /rate_limit per credential, which is authenticated and
 * consumes no quota, so this is safe to run on a schedule.
 *
 * github-authentication-token-expiration is the only place the date is
 * readable, and it is readable only while the credential still works.
 */
const API = 'https://api.github.com';
const UA = 'github-token-expiry-watch/1.0';

export const HEADER = 'github-authentication-token-expiration';
export const DAY = 86400;

// Under two hours on a working credential is a minted App installation token,
// which is the desired end state. It is also a PAT in its final two hours, and
// the header cannot tell them apart.
export const SHORT_LIVED_S = 2 * 3600;

// Notice, warning, critical. One alarm at zero is not monitoring.
export const DEFAULT_THRESHOLDS = [30, 14, 3];

const STAMP = /^(\d{4})-(\d{2})-(\d{2})(?:[ ](\d{2}):(\d{2})(?::(\d{2}))?)?$/;
const OFFSET = /([+-]\d{2}:?\d{2})$/;

/**
 * Epoch seconds from the expiry header, or null. Pure.
 * Anything that does not parse returns null rather than a plausible wrong date.
 */
export function parseExpiry(value) {
  if (typeof value !== 'string') return null;
  // Only the ISO separator, never any other T: the documented shape ends in
  // 'UTC', and a blanket replace turns that into 'U C'.
  let text = value.trim().replace(/^(\d{4}-\d{2}-\d{2})T/, '$1 ');
  if (!text) return null;

  let offset = '+0000';
  const upper = text.toUpperCase();
  if (upper.endsWith(' UTC') || upper.endsWith(' GMT')) {
    text = text.slice(0, -4).trim();
  } else if (upper.endsWith('Z')) {
    text = text.slice(0, -1).trim();
  } else {
    const found = text.match(OFFSET);
    if (found) {
      offset = found[1].replace(':', '');
      text = text.slice(0, found.index).trim();
    }
  }

  const stamp = text.match(STAMP);
  if (!stamp) return null;
  const [, year, month, day, hour, minute, second] = stamp;
  const sign = offset[0] === '-' ? 1 : -1;
  const shift = sign * (Number(offset.slice(1, 3)) * 3600 + Number(offset.slice(3, 5)) * 60);
  const base = Date.UTC(Number(year), Number(month) - 1, Number(day),
    Number(hour ?? 0), Number(minute ?? 0), Number(second ?? 0)) / 1000;
  return base + shift;
}

/** Case-insensitive header lookup. Pure. */
export function headerValue(headers, name = HEADER) {
  for (const [key, value] of Object.entries(headers ?? {})) {
    if (String(key).toLowerCase() === name) return value;
  }
  return null;
}

/** Seconds between now and the expiry; null when either is unreadable. Pure. */
export function secondsLeft(expiry, now) {
  const e = Number.parseInt(expiry, 10);
  const n = Number.parseInt(now, 10);
  if (!Number.isFinite(e) || !Number.isFinite(n)) return null;
  return e - n;
}

/** Name the urgency of a remaining lifetime. Pure. */
export function bucket(remaining, thresholds = DEFAULT_THRESHOLDS) {
  if (remaining === null || remaining === undefined) return 'unknown';
  if (remaining <= 0) return 'expired';
  if (remaining < SHORT_LIVED_S) return 'short-lived';
  const [notice, warning, critical] = thresholds;
  const days = remaining / DAY;
  if (days <= critical) return 'critical';
  if (days <= warning) return 'warning';
  if (days <= notice) return 'notice';
  return 'ok';
}

/** One credential's expiry reading, including why there might not be one. Pure. */
export function reading(name, status, headers, now, thresholds = DEFAULT_THRESHOLDS) {
  const code = Number.parseInt(status, 10);
  const value = Number.isFinite(code) ? code : 0;

  if (value === 401) {
    return { name, state: 'rejected', seconds_left: null,
      why: 'the credential was refused, so its expiry is no longer a forecast' };
  }
  if (!(value >= 200 && value < 300)) {
    return { name, state: 'unreadable', seconds_left: null,
      why: `the probe returned ${value}, so nothing can be read from its headers` };
  }

  const raw = headerValue(headers);
  if (raw === null || raw === undefined) {
    return { name, state: 'no-expiry-reported', seconds_left: null,
      why: 'the request succeeded and carried no expiry header, which means ' +
        'either the credential never expires or its class does not report one. ' +
        'The header cannot tell those apart' };
  }

  const expiry = parseExpiry(raw);
  if (expiry === null) {
    return { name, state: 'unreadable-header', seconds_left: null,
      why: `the expiry header was present but did not parse: '${raw}'` };
  }

  const remaining = secondsLeft(expiry, now);
  return { name, state: bucket(remaining, thresholds), seconds_left: remaining,
    expires_at: expiry, why: `read from the ${HEADER} response header` };
}

// Urgency first. An unreadable credential outranks one with ninety days left.
export const ORDER = {
  expired: 0, critical: 1, warning: 2, rejected: 3, 'unreadable-header': 4,
  unreadable: 5, 'no-expiry-reported': 6, notice: 7, 'short-lived': 8, ok: 9,
  unknown: 10,
};

/** Order the readings by urgency, then by soonest. Pure. */
export function schedule(rows) {
  const rank = (row) => [
    ORDER[row.state] ?? 99,
    Number.isInteger(row.seconds_left) ? row.seconds_left : 2 ** 30,
    String(row.name),
  ];
  return [...(rows ?? [])].sort((a, b) => {
    const x = rank(a);
    const y = rank(b);
    for (let i = 0; i < x.length; i += 1) {
      if (x[i] < y[i]) return -1;
      if (x[i] > y[i]) return 1;
    }
    return 0;
  });
}

/** The one line to act on. Pure. */
export function verdict(ordered) {
  if (!ordered || !ordered.length) {
    return ['nothing-checked', 'no credentials were named, so nothing was checked.'];
  }
  const top = ordered[0];
  const { state, name } = top;
  const remaining = top.seconds_left;

  if (state === 'expired') {
    return ['expired',
      `${name} has already passed its expiry. It will be answering 401 Bad ` +
      'credentials, identically to a credential that was revoked.'];
  }
  if (['critical', 'warning', 'notice'].includes(state)) {
    return [state,
      `${name} expires in ${(remaining / DAY).toFixed(1)} day(s). Alert at 30, ` +
      '14 and 3 days rather than at zero.'];
  }
  if (state === 'short-lived') {
    return ['short-lived',
      `${name} expires in ${Math.floor(remaining / 60)} minute(s), which is what ` +
      'a freshly minted GitHub App installation token looks like and is a ' +
      'non-event. It is also what a personal access token in its final two hours ' +
      'looks like, and the header does not distinguish them.'];
  }
  if (state === 'rejected') {
    return ['rejected',
      `${name} was refused, so there is no expiry left to forecast. Whether it ` +
      'expired or was revoked is not observable from here.'];
  }
  if (state === 'unreadable' || state === 'unreadable-header') {
    return ['unreadable', `${name} could not be read: ${top.why}`];
  }
  if (state === 'no-expiry-reported') {
    return ['no-expiry-reported',
      `${name} reported no expiry. Either it never expires, which is a larger ` +
      'standing risk than one that does, or its class does not surface a date. ' +
      'Find out which before calling it healthy.'];
  }
  return ['ok',
    `the soonest expiry is ${name} at ${((remaining ?? 0) / DAY).toFixed(1)} day(s).`];
}

async function probe(name, token) {
  try {
    const res = await fetch(`${API}/rate_limit`, {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': UA,
      },
    });
    const headers = {};
    for (const [k, v] of res.headers.entries()) headers[k.toLowerCase()] = v;
    return { status: res.status, headers };
  } catch (err) {
    console.error(`${name}: request failed: ${err.message}`);
    return { status: 0, headers: {} };
  }
}

async function main() {
  const names = process.argv.slice(2);
  const wanted = names.length ? names : ['GITHUB_TOKEN'];
  const now = Math.floor(Date.now() / 1000);

  const rows = [];
  for (const name of wanted) {
    const token = process.env[name];
    if (!token) {
      rows.push({ name, state: 'unreadable', seconds_left: null,
        why: 'the environment variable is not set' });
      continue;
    }
    const { status, headers } = await probe(name, token);
    rows.push(reading(name, status, headers, now));
  }

  const ordered = schedule(rows);
  for (const row of ordered) {
    const left = row.seconds_left === null ? '-' : `${(row.seconds_left / DAY).toFixed(1)} day(s)`;
    console.log(`${row.name.padEnd(20)} ${row.state.padEnd(20)} ${left.padStart(12)}  ${row.why ?? ''}`);
  }

  const [state, detail] = verdict(ordered);
  console.log(`${state}: ${detail}`);

  if (['critical', 'warning', 'expired'].includes(state)) {
    console.log('repair: rotate now, and record the new expiry in the same place ' +
      'the secret is stored so the next person sees it.');
  }
  if (['critical', 'warning', 'notice', 'expired', 'no-expiry-reported'].includes(state)) {
    console.log('repair: for automation with no human owner, authenticate as a ' +
      'GitHub App installation; its tokens live about an hour and need no diary entry.');
  }

  console.log(JSON.stringify({ state, readings: ordered }, null, 2));
  process.exitCode = ['ok', 'short-lived', 'nothing-checked'].includes(state) ? 0 : 1;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err); process.exitCode = 2; });
}
