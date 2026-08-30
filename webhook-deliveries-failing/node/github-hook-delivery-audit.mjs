/**
 * Report GitHub webhooks whose deliveries are failing, and say how they fail.
 *
 * Read only. Every request is a GET. The redelivery call is printed for a human
 * to run, never made here.
 */
const API = 'https://api.github.com';
const UA = 'github-hook-delivery-audit/1.0';

// Failure buckets, most diagnostic first. Ties are broken by this order.
const FAILURE_ORDER = ['rejected', 'server-error', 'timeout', 'unreachable',
  'client-error', 'unknown'];

/**
 * Sort one delivery record into a bucket. Pure. A record with no status code
 * never reached a server; one with 401 or 403 reached one that refused it.
 */
export function bucket(delivery) {
  const status = String(delivery.status ?? '').trim().toLowerCase();
  const code = Number.parseInt(delivery.status_code, 10) || 0;
  if (code >= 200 && code < 300) return 'ok';
  if (status.includes('tim')) return 'timeout';
  if (!code) return 'unreachable';
  if (code === 401 || code === 403) return 'rejected';
  if (code >= 400 && code < 500) return 'client-error';
  if (code >= 500 && code < 600) return 'server-error';
  return 'unknown';
}

/**
 * Read the hook's last_response: the one-request version of this whole check.
 * A null code means the hook has never delivered anything, which is not a
 * failure.
 */
export function triage(hook) {
  const last = hook.last_response ?? {};
  if (last.code === null || last.code === undefined) {
    return ['never', 'no delivery attempt recorded yet'];
  }
  const code = Number.parseInt(last.code, 10);
  if (!Number.isFinite(code)) {
    return ['unknown', `unreadable last_response code ${JSON.stringify(last.code)}`];
  }
  if (code >= 200 && code < 300) return ['ok', `last attempt returned ${code}`];
  const message = String(last.message ?? '').trim();
  return ['failing',
    `last attempt returned ${code}${message ? `: ${message}` : ''}`];
}

/** Count deliveries by bucket and keep the ends of the window. Pure. */
export function summarize(deliveries) {
  const out = {
    total: 0, ok: 0, failed: 0, redeliveries: 0, counts: {}, guids: {},
    last_ok: null, first_failed: null, last_failed: null,
  };
  for (const d of deliveries ?? []) {
    const kind = bucket(d);
    const when = String(d.delivered_at ?? '');
    out.total += 1;
    if (d.redelivery) out.redeliveries += 1;
    if (kind === 'ok') {
      out.ok += 1;
      if (when && (out.last_ok === null || when > out.last_ok)) out.last_ok = when;
      continue;
    }
    out.failed += 1;
    out.counts[kind] = (out.counts[kind] ?? 0) + 1;
    const ids = (out.guids[kind] ??= []);
    if (ids.length < 5 && d.id !== undefined && d.id !== null) ids.push(d.id);
    if (when) {
      if (out.first_failed === null || when < out.first_failed) out.first_failed = when;
      if (out.last_failed === null || when > out.last_failed) out.last_failed = when;
    }
  }
  return out;
}

/** Classify one hook from its delivery summary. Pure. Returns [state, detail]. */
export function verdict(summary) {
  const total = summary.total ?? 0;
  if (!total) {
    return ['empty',
      'no deliveries in the retained window. Either nothing this hook ' +
      'subscribes to has happened, or the hook is not active.'];
  }
  const failed = summary.failed ?? 0;
  if (!failed) return ['clean', `${total} delivery(ies), all accepted`];

  if (summary.last_ok && summary.last_failed && summary.last_ok > summary.last_failed) {
    return ['recovered',
      `${failed} of ${total} failed, but the most recent delivery succeeded. ` +
      `The receiver is working; ${failed} event(s) are still waiting on a replay.`];
  }

  const counts = summary.counts ?? {};
  let worst = null;
  for (const kind of FAILURE_ORDER) {
    const n = counts[kind] ?? 0;
    if (n && (worst === null || n > counts[worst])) worst = kind;
  }
  const n = counts[worst] ?? 0;

  if (worst === 'rejected') {
    return [worst,
      `${n} of ${total} came back 401 or 403. Your own server refused GitHub. ` +
      'This is the only shape a mismatched webhook secret takes from outside: ' +
      'the API will not compare secrets for you.'];
  }
  if (worst === 'server-error') {
    return [worst,
      `${n} of ${total} returned 5xx. The payload arrived and the handler ` +
      'raised, so the trace is in your application, not in the network.'];
  }
  if (worst === 'timeout') {
    return [worst,
      `${n} of ${total} timed out. GitHub allows a receiver 10 seconds; a ` +
      'handler doing its real work synchronously runs past that as soon as the ' +
      'payload grows.'];
  }
  if (worst === 'unreachable') {
    return [worst,
      `${n} of ${total} recorded no status code at all, so nothing answered: ` +
      "DNS, TLS, a closed port, or an allow-list that no longer matches GitHub's " +
      'hook ranges.'];
  }
  return [worst ?? 'unknown',
    `${n} of ${total} failed with a 4xx that is not an auth error, which is ` +
    'usually a route that moved (404) or a body the handler would not parse (400).'];
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
      'admin:repo_hook (or the fine-grained Webhooks: Read permission). GitHub ' +
      'returns 404 rather than 403 when a token cannot see a resource at all.');
  }
  if (!res.ok) throw new Error(`${res.status} from ${url}`);
  return res;
}

async function page(token, url, limit = 1000) {
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
    console.error('usage: node github-hook-delivery-audit.mjs owner/name');
    process.exitCode = 2;
    return;
  }

  const base = `${API}/repos/${repo}/hooks`;
  const hooks = await page(token, `${base}?per_page=100`);
  if (hooks.length === 0) {
    console.log(`no webhooks on ${repo} that this token can see`);
    return;
  }

  let failing = 0;
  let replayable = 0;
  for (const hook of hooks) {
    const url = hook.config?.url ?? '?';
    const [tstate, tdetail] = triage(hook);
    console.log(`hook ${hook.id} ${url}  last_response: ${tstate} (${tdetail})`);

    const deliveries = await page(token, `${base}/${hook.id}/deliveries?per_page=100`, 300);
    const summary = summarize(deliveries);
    const [state, detail] = verdict(summary);
    const line = `  ${state.padEnd(12)} ${detail}`;
    if (state === 'clean' || state === 'empty') { console.log(line); continue; }

    console.warn(line);
    console.warn(`  failures from ${summary.first_failed} to ${summary.last_failed}, ` +
      `${summary.redeliveries} redelivery(ies) already in the log`);
    if (state !== 'recovered') failing += 1;
    replayable += summary.failed;
    for (const [kind, ids] of Object.entries(summary.guids).sort()) {
      for (const id of ids) {
        console.warn(`  repair: POST ${base}/${hook.id}/deliveries/${id}/attempts  (${kind})`);
      }
    }
  }

  console.log(`${hooks.length} hook(s), ${failing} failing, ` +
    `${replayable} delivery(ies) needing a replay`);
  process.exitCode = failing ? 1 : 0;
}

// Only run when invoked directly. The test file imports this module, and without
// the guard main() would run there too, fail on the missing token, and set a
// non-zero exit code that fails the whole test file even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
