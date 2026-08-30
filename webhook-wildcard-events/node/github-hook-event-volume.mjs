/**
 * Quantify what a wildcard webhook subscription costs a receiver.
 *
 * Read only. GETs the repository's hooks and their delivery records, tallies
 * the deliveries by event type, and reports the fraction that came from events
 * the receiver does not implement. Nothing is created, edited or removed: the
 * script prints the explicit events list to install in place of the wildcard.
 *
 * Environment:
 *   GITHUB_TOKEN           a read-only token with access to the repository
 *   GITHUB_REPO            owner/repo
 *   GITHUB_HANDLED_EVENTS  comma separated events the receiver implements
 */
const API = 'https://api.github.com';
const UA = 'github-hook-event-volume/1.0';

export const WILDCARD = '*';

/** One event name, lowercased and trimmed. Pure. */
export function normalize(name) {
  return String(name ?? '').trim().toLowerCase();
}

/** The normalised events array on a hook. Pure. */
export function subscribed(hook) {
  if (!hook || typeof hook !== 'object' || !Array.isArray(hook.events)) return [];
  return hook.events.map(normalize).filter(Boolean);
}

/** Whether this subscription is open ended. Pure. */
export function isWildcard(events) {
  return (events || []).map(normalize).includes(WILDCARD);
}

/** The events a receiver implements, as a normalised set. Pure. */
export function handledSet(names) {
  const list = typeof names === 'string' ? names.replace(/;/g, ',').split(',') : (names || []);
  return new Set([...list].map(normalize).filter((e) => e && e !== WILDCARD));
}

/** Deliveries by event type. Pure. */
export function tally(rows) {
  const counts = {};
  for (const row of rows || []) {
    if (!row || typeof row !== 'object') continue;
    const event = normalize(row.event) || 'unknown';
    counts[event] = (counts[event] || 0) + 1;
  }
  return counts;
}

/** How much of the delivered volume the receiver discards. Pure. */
export function waste(counts, handled) {
  const table = counts || {};
  const wanted = handledSet(handled instanceof Set ? [...handled] : handled);
  const total = Object.values(table).reduce((a, b) => a + b, 0);
  const unwanted = Object.entries(table).filter(([e]) => !wanted.has(e));
  const discarded = unwanted.reduce((a, [, n]) => a + n, 0);
  return {
    total,
    unhandled_deliveries: discarded,
    unhandled_events: unwanted.map(([e]) => e).sort(),
    share: total ? Math.round((1000 * discarded) / total) / 10 : 0,
  };
}

/** The explicit events list to install in place of the wildcard. Pure. */
export function proposedEvents(handled) {
  return [...handledSet(handled instanceof Set ? [...handled] : handled)].sort();
}

/** Handled events with no deliveries in the window. Pure. */
export function neverSeen(counts, handled) {
  const table = counts || {};
  return [...handledSet(handled instanceof Set ? [...handled] : handled)]
    .filter((e) => !table[e]).sort();
}

/** Turn the subscription and the volume into a finding. Pure. */
export function verdict(events, counts, handled) {
  const subs = new Set((events || []).map(normalize).filter(Boolean));
  const wanted = handledSet(handled instanceof Set ? [...handled] : handled);
  if (subs.has(WILDCARD)) {
    const w = waste(counts, wanted);
    if (!w.total) {
      return ['wildcard-unmeasured',
        'this hook subscribes to every event with *, and no deliveries in the '
        + 'retained window let the volume be measured. The subscription is open '
        + 'ended either way: every event type GitHub ships next joins it.'];
    }
    if (w.unhandled_deliveries) {
      return ['wildcard',
        `${w.unhandled_deliveries} of ${w.total} deliveries (${w.share.toFixed(1)}%) `
        + 'were events this receiver does not implement, and * also subscribes to '
        + 'every event type GitHub ships next.'];
    }
    return ['wildcard-all-handled',
      'every delivery in the window happened to be an event this receiver '
      + 'implements, which is luck rather than design: * subscribes to event '
      + 'types that do not exist yet.'];
  }
  const extra = [...subs].filter((e) => !wanted.has(e)).sort();
  if (extra.length) {
    return ['over-subscribed',
      `this hook subscribes to ${extra.length} event(s) the receiver does not `
      + `implement: ${extra.join(', ')}.`];
  }
  if (!subs.size) {
    return ['no-events',
      'this hook has an empty events array, so nothing is delivered to it at all.'];
  }
  return ['tight', 'every subscribed event is one the receiver implements.'];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state, handled = null, counts = null) {
  const listing = JSON.stringify(proposedEvents(handled));
  if (['wildcard', 'wildcard-unmeasured', 'wildcard-all-handled'].includes(state)) {
    const pending = neverSeen(counts, handled);
    const caution = pending.length
      ? ` Keep ${pending.join(', ')} on the list even though nothing arrived for them in this window.`
      : '';
    return `replace ["*"] with ${listing}, which bounds the subscription and stops `
      + `new event types joining it without a decision.${caution}`;
  }
  if (state === 'over-subscribed') {
    return `narrow the events array to ${listing}. Nothing is failing; this is `
      + 'volume the receiver pays for and discards.';
  }
  if (state === 'no-events') {
    return `add the events the receiver implements: ${listing}. An empty array `
      + 'delivers nothing.';
  }
  return 'nothing. The subscription matches what the receiver handles.';
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
  const handled = handledSet((process.env.GITHUB_HANDLED_EVENT || "dummy-github-handled-event")S || '');
  if (!token || !repo || !repo.includes('/') || !handled.size) {
    console.error('set GITHUB_TOKEN, GITHUB_REPO=owner/repo and GITHUB_HANDLED_EVENTS');
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

  const findings = [];
  for (const hook of hooksRes.body) {
    const events = subscribed(hook);
    console.log(`hook ${hook.id} -> ${(hook.config || {}).url || '?'}`);
    console.log(`subscribed: ${isWildcard(events) ? '* (wildcard)' : events.join(', ') || 'nothing'}`);
    const rows = [];
    let url = `/repos/${owner}/${name}/hooks/${hook.id}/deliveries?per_page=100`;
    for (let page = 0; page < 8 && url; page += 1) {
      const res = await get(token, url);
      if (res.status !== 200 || !Array.isArray(res.body)) break;
      rows.push(...res.body);
      url = nextLink(res.headers);
    }
    const counts = tally(rows);
    const [state, detail] = verdict(events, counts, handled);
    console.log(`${state}: ${detail}`);
    console.log(`repair: ${repair(state, handled, counts)}`);
    findings.push({
      hook_id: hook.id, events, wildcard: isWildcard(events), state,
      counts, waste: waste(counts, handled), proposed_events: proposedEvents(handled),
    });
  }
  console.log(JSON.stringify({ repo, handled: [...handled].sort(), hooks: findings }, null, 2));
  process.exitCode = findings.every((f) => f.state === 'tight') ? 0 : 1;
}

// Guarded so importing this file from the test runner does not start a run.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
