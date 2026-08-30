/**
 * Report how close a webhook receiver is to the 10 second delivery cutoff.
 *
 * Read only. GETs the repository's hooks and their delivery records. Nothing is
 * sent to the receiver: timing it from here would be a write, and a worse
 * measurement than the one GitHub already recorded from its own side.
 *
 * Environment:
 *   GITHUB_TOKEN    a read-only token with access to the repository
 *   GITHUB_REPO     owner/repo
 *   GITHUB_HOOK_ID  optional, one hook instead of all of them
 */
const API = 'https://api.github.com';
const UA = 'github-hook-delivery-duration/1.0';

export const CUTOFF_MS = 10000;
export const WARN_MS = 8000;
export const SLOW_MS = 5000;
// The duration field carries no unit; at or under sixty it is seconds.
export const SECONDS_CEILING = 60;

/** A delivery's duration in milliseconds, or null. Pure. */
export function durationMs(row) {
  if (!row || typeof row !== 'object') return null;
  const raw = row.duration;
  if (raw === null || raw === undefined || typeof raw === 'boolean') return null;
  const value = Number(raw);
  if (!Number.isFinite(value) || value < 0) return null;
  return value <= SECONDS_CEILING ? value * 1000 : value;
}

/** Whether GitHub abandoned this delivery. Pure. */
export function timedOut(row) {
  if (!row || typeof row !== 'object') return false;
  const status = String(row.status ?? '').toLowerCase().split(/\s+/).join(' ');
  if (status.includes('timed out') || status.includes('timeout')) return true;
  const ms = durationMs(row);
  return ms !== null && ms >= CUTOFF_MS;
}

/** Sort one delivery by how much room it had left. Pure. */
export function classify(row) {
  if (timedOut(row)) return 'timed-out';
  const ms = durationMs(row);
  if (ms === null) return 'unknown';
  if (ms >= WARN_MS) return 'at-risk';
  if (ms >= SLOW_MS) return 'slow';
  return 'fine';
}

/** Nearest-rank percentile over a list of numbers, or null. Pure. */
export function percentile(values, p) {
  const numbers = (values || []).filter((v) => typeof v === 'number' && Number.isFinite(v))
    .sort((a, b) => a - b);
  if (!numbers.length) return null;
  if (p <= 0) return numbers[0];
  if (p >= 100) return numbers[numbers.length - 1];
  const rank = Math.max(1, Math.ceil((p / 100) * numbers.length));
  return numbers[Math.min(rank, numbers.length) - 1];
}

/** The distribution that decides the verdict. Pure. */
export function stats(rows) {
  const list = (rows || []).filter((r) => r && typeof r === 'object');
  const measured = list.map(durationMs).filter((m) => m !== null);
  const p95 = percentile(measured, 95);
  return {
    count: list.length,
    measured: measured.length,
    timed_out: list.filter(timedOut).length,
    p50: percentile(measured, 50),
    p95,
    max: measured.length ? Math.max(...measured) : null,
    headroom_ms: p95 === null ? null : CUTOFF_MS - p95,
  };
}

/** The same distribution per event type. Pure. */
export function byEvent(rows, minCount = 3) {
  const groups = {};
  for (const row of rows || []) {
    if (!row || typeof row !== 'object') continue;
    const event = String(row.event ?? 'unknown').trim().toLowerCase() || 'unknown';
    (groups[event] = groups[event] || []).push(row);
  }
  const out = {};
  for (const [event, group] of Object.entries(groups)) {
    const measured = group.map(durationMs).filter((m) => m !== null);
    const row = {
      count: group.length,
      timed_out: group.filter(timedOut).length,
      p95: percentile(measured, 95),
    };
    if (row.count >= minCount || row.timed_out) out[event] = row;
  }
  return out;
}

/** The event type with the worst tail, or null. Pure. */
export function slowestEvent(rows, minCount = 3) {
  const grouped = byEvent(rows, minCount);
  const ranked = Object.entries(grouped).filter(([, v]) => v.p95 !== null);
  if (!ranked.length) return null;
  ranked.sort((a, b) => b[1].p95 - a[1].p95 || a[0].localeCompare(b[0]));
  const [event, row] = ranked[0];
  return { event, p95: row.p95, count: row.count, timed_out: row.timed_out };
}

/** Turn the distribution into a finding. Pure. */
export function verdict(st) {
  if (!st || !st.count) {
    return ['no-data',
      'no deliveries in the retained window, so there is nothing to measure. '
      + 'That is not the same as a receiver that is fast.'];
  }
  if (!st.measured) {
    return ['no-durations',
      `${st.count} delivery/deliveries carry no duration, so the tail cannot be `
      + 'measured from this feed.'];
  }
  const p95 = st.p95;
  if (st.timed_out) {
    return ['timing-out',
      `${st.timed_out} deliveries were abandoned at the 10 second cutoff, and `
      + `the 95th percentile is ${Math.trunc(p95)}ms, which leaves `
      + `${Math.trunc(st.headroom_ms)}ms of headroom on everything else.`];
  }
  if (p95 >= WARN_MS) {
    return ['at-the-edge',
      `nothing has timed out yet and the 95th percentile is ${Math.trunc(p95)}ms, `
      + `leaving ${Math.trunc(st.headroom_ms)}ms before the cutoff. This fails on `
      + 'the next slow week.'];
  }
  if (p95 >= SLOW_MS) {
    return ['slow',
      `the 95th percentile is ${Math.trunc(p95)}ms against a 10 second cutoff. `
      + `The handler is doing real work inline and has ${Math.trunc(st.headroom_ms)}ms of room.`];
  }
  return ['healthy',
    `the 95th percentile is ${Math.trunc(p95)}ms, ${Math.trunc(st.headroom_ms)}ms inside the cutoff.`];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state, worst = null) {
  if (['timing-out', 'at-the-edge', 'slow'].includes(state)) {
    const target = worst
      ? ` Start with ${worst.event}, whose 95th percentile is ${Math.trunc(worst.p95)}ms.`
      : '';
    return 'verify the signature, put the raw payload on a queue, return 202, and '
      + 'do the work in a worker keyed on the delivery guid so a redelivery cannot '
      + `run it twice.${target}`;
  }
  if (state === 'no-data') {
    return 'nothing to repair, and nothing proved either. Check the hook is '
      + 'active and that the retention window covers a period when events '
      + 'actually happened.';
  }
  if (state === 'no-durations') {
    return 'read the durations from a wider page of deliveries; this window has '
      + 'statuses but no timings to work from.';
  }
  return 'nothing. The receiver answers well inside the cutoff.';
}

/** The rel=next URL from a Link header, or null. Pure. */
export function nextLink(headers) {
  const link = (headers && (headers.get ? headers.get('link') : headers.Link || headers.link)) || '';
  for (const part of String(link).split(',')) {
    const url = part.split(';')[0].trim();
    if (part.includes('rel="next"') && url.startsWith('<') && url.endsWith('>')) {
      return url.slice(1, -1);
    }
  }
  return null;
}

function headersFor(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
}

async function get(token, url) {
  const res = await fetch(url.startsWith('/') ? API + url : url, { headers: headersFor(token) });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return { status: res.status, body, headers: res.headers };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const repo = (process.env.GITHUB_REPO || "dummy-github-repo");
  if (!token || !repo || !repo.includes('/')) {
    console.error('set GITHUB_TOKEN and GITHUB_REPO=owner/repo');
    process.exitCode = 2;
    return;
  }
  const [owner, name] = repo.split('/');
  const hooksRes = await get(token, `/repos/${owner}/${name}/hooks?per_page=100`);
  if (hooksRes.status !== 200 || !Array.isArray(hooksRes.body)) {
    console.error(`GET hooks returned ${hooksRes.status}`);
    process.exitCode = 2;
    return;
  }
  const wanted = (process.env.GITHUB_HOOK_ID || "dummy-github-hook-id");
  const hooks = wanted
    ? hooksRes.body.filter((h) => String(h.id) === String(wanted))
    : hooksRes.body;

  let worstState = 'healthy';
  const report = [];
  for (const hook of hooks) {
    console.log(`hook ${hook.id} -> ${(hook.config || {}).url || '?'}`);
    const rows = [];
    let url = `/repos/${owner}/${name}/hooks/${hook.id}/deliveries?per_page=100`;
    for (let page = 0; page < 8 && url; page += 1) {
      const res = await get(token, url);
      if (res.status !== 200 || !Array.isArray(res.body)) {
        console.error(`deliveries returned ${res.status}`);
        break;
      }
      rows.push(...res.body);
      url = nextLink(res.headers);
    }
    const st = stats(rows);
    const [state, detail] = verdict(st);
    const worst = slowestEvent(rows);
    console.log(`${st.count} delivery/deliveries, ${st.timed_out} timed out, `
      + `p95 ${st.p95 === null ? '?' : Math.trunc(st.p95)}ms`);
    console.log(`${state}: ${detail}`);
    if (worst) {
      console.log(`slowest event: ${worst.event}, p95 ${Math.trunc(worst.p95)}ms `
        + `across ${worst.count} deliveries`);
    }
    console.log(`repair: ${repair(state, worst)}`);
    if (['timing-out', 'at-the-edge'].includes(state)) worstState = state;
    report.push({ hook_id: hook.id, stats: st, state, slowest_event: worst });
  }
  console.log(JSON.stringify({ repo, hooks: report }, null, 2));
  process.exitCode = worstState === 'healthy' ? 0 : 1;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
