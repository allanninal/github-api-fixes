/**
 * Compare the events a webhook is subscribed to against the ones you handle.
 *
 * Read only. Two GETs per hook: the hook list, and one page of its delivery log.
 * An unsubscribed event produces no failure and no delivery record, so the only
 * way to find one is to compare two lists.
 */
const API = 'https://api.github.com';
const UA = 'github-hook-event-coverage/1.0';

/**
 * Canonical form of an event name. Pure. GitHub names events with underscores,
 * URLs use hyphens, and handlers are often registered under an action
 * (pull_request.opened), which is a payload field and not a subscription.
 */
export function normalize(name) {
  const base = String(name ?? '').trim().toLowerCase().replaceAll('-', '_');
  return base.includes('.') ? base.slice(0, base.indexOf('.')) : base;
}

/**
 * Compare handlers, subscriptions and observed traffic. Pure. States:
 * missing, delivered, quiet, wildcard, unhandled.
 */
export function coverage(handled, subscribed, seen = []) {
  const subs = new Map();
  let wildcard = false;
  for (const raw of subscribed ?? []) {
    if (String(raw).trim() === '*') { wildcard = true; continue; }
    subs.set(normalize(raw), String(raw));
  }

  const seenEvents = new Map();
  for (const raw of seen ?? []) {
    const key = normalize(raw);
    seenEvents.set(key, (seenEvents.get(key) ?? 0) + 1);
  }

  const rows = [];
  const claimed = new Set();
  for (const raw of handled ?? []) {
    const key = normalize(raw);
    claimed.add(key);
    const note = String(raw) !== key
      ? `your handler is registered as '${raw}'; GitHub spells this '${key}'`
      : '';
    let state;
    if (wildcard) state = 'wildcard';
    else if (!subs.has(key)) state = 'missing';
    else if (seenEvents.has(key)) state = 'delivered';
    else state = 'quiet';
    rows.push({ event: key, handler: String(raw), state,
      seen: seenEvents.get(key) ?? 0, note });
  }

  for (const key of [...new Set([...subs.keys(), ...seenEvents.keys()])].sort()) {
    if (claimed.has(key)) continue;
    rows.push({ event: key, handler: null, state: 'unhandled',
      seen: seenEvents.get(key) ?? 0,
      note: subs.has(key) ? 'subscribed' : 'arriving without a subscription' });
  }
  return rows;
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
    throw new Error(`${res.status} from ${url}: reading hooks needs ` +
      'admin:repo_hook, and GitHub answers 404 rather than 403 when the token ' +
      'cannot see the resource at all');
  }
  if (!res.ok) throw new Error(`${res.status} from ${url}`);
  return res;
}

async function page(token, url, limit = 500) {
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
  const [repo, ...handles] = process.argv.slice(2);
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN (a read-only token is enough)');
    process.exitCode = 2;
    return;
  }
  if (!repo || !repo.includes('/') || handles.length === 0) {
    console.error('usage: node github-hook-event-coverage.mjs owner/name push pull_request');
    process.exitCode = 2;
    return;
  }

  const base = `${API}/repos/${repo}/hooks`;
  const hooks = await page(token, `${base}?per_page=100`);
  if (hooks.length === 0) {
    console.log(`no webhooks on ${repo} that this token can see`);
    return;
  }

  let missing = 0;
  let unhandled = 0;
  for (const hook of hooks) {
    const url = hook.config?.url ?? '?';
    const subscribed = hook.events ?? [];
    const deliveries = await page(token,
      `${base}/${hook.id}/deliveries?per_page=100`, 200);
    const seen = deliveries.map((d) => d.event);
    console.log(`hook ${hook.id} ${url}  subscribes to ${subscribed.length} ` +
      `event(s), ${deliveries.length} delivery(ies) read`);

    for (const row of coverage(handles, subscribed, seen)) {
      const line = `  ${row.state.padEnd(10)} ${row.event}` +
        (row.note ? `  ${row.note}` : '');
      if (row.state === 'delivered' || row.state === 'quiet') {
        console.log(line);
        continue;
      }
      console.warn(line);
      if (row.state === 'missing') {
        missing += 1;
        console.warn(`     repair: add '${row.event}' to this hook's events ` +
          'array; until then the handler cannot run and nothing will report an error');
      } else if (row.state === 'unhandled') {
        unhandled += 1;
        console.warn(`     ${row.seen} delivery(ies) of an event nothing ` +
          'handles: volume you receive, verify and discard');
      } else if (row.state === 'wildcard') {
        console.warn('     the hook subscribes to *, so this arrives along with ' +
          'every event type GitHub adds in future');
      }
    }
  }

  console.log(`${hooks.length} hook(s), ${missing} handler(s) with no ` +
    `subscription, ${unhandled} unhandled event(s) arriving`);
  process.exitCode = missing ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not run main(), fail on the missing token, and fail the test file with it.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
