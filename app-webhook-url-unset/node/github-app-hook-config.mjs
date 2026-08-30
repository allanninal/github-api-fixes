/**
 * Say whether a GitHub App has a webhook destination that can work.
 *
 * Read only. Three GETs against the App itself: its own record, its webhook
 * configuration, and a page of its deliveries. Nothing is created or changed.
 *
 * Authentication is a JWT signed with the App's private key, taken from the
 * environment. The key never enters this process.
 *
 * Environment:
 *   GITHUB_APP_JWT   a JWT signed with the App's private key
 */
const API = 'https://api.github.com';
const UA = 'github-app-hook-config/1.0';

/** The four ways this actually happens, none of which is an empty field. */
export const PLACEHOLDER_HOSTS = ['example.com', 'example.org', 'example.net',
  'your-domain.com', 'yourdomain.com', 'changeme', 'todo'];
export const TUNNEL_HOSTS = ['smee.io', 'ngrok.io', 'ngrok-free.app', 'ngrok.app',
  'loca.lt', 'trycloudflare.com', 'serveo.net'];
export const LOOPBACK_HOSTS = ['localhost', '127.0.0.1', '0.0.0.0', '::1'];
export const DEFAULT_STALE_DAYS = 30;

/** The lowercase hostname of a URL, or an empty string. Pure. */
export function hostOf(url) {
  try {
    return new URL(String(url ?? '').trim()).hostname.toLowerCase().replace('[', '').replace(']', '');
  } catch {
    return '';
  }
}

/** Whether a host is one of these names or a subdomain of one. Pure. */
export function hostMatches(host, suffixes) {
  return (suffixes || []).some((suffix) => host === suffix || host.endsWith(`.${suffix}`));
}

/** Sort a webhook destination into what it can actually reach. Pure. */
export function urlClass(url) {
  const raw = String(url ?? '').trim();
  if (!raw) return 'unset';
  let parsed = null;
  try { parsed = new URL(raw); } catch { return 'malformed'; }
  const host = hostOf(raw);
  if (!['http:', 'https:'].includes(parsed.protocol) || !host) return 'malformed';
  if (hostMatches(host, PLACEHOLDER_HOSTS) || host.startsWith('example.')) return 'placeholder';
  if (hostMatches(host, TUNNEL_HOSTS)) return 'tunnel';
  if (LOOPBACK_HOSTS.includes(host) || host.endsWith('.local')) return 'loopback';
  if (parsed.protocol === 'http:') return 'insecure';
  return 'production';
}

/** Whether a secret is set. Never returns the value. Pure. */
export function secretState(config) {
  if (!config || typeof config !== 'object') return 'unknown';
  return config.secret !== null && config.secret !== undefined ? 'set' : 'absent';
}

/** The App hook's body encoding, with the documented default applied. Pure. */
export function contentTypeOf(config) {
  if (!config || typeof config !== 'object') return 'unknown';
  const raw = config.content_type;
  if (raw === null || raw === undefined) return 'form';
  const value = String(raw).trim().toLowerCase();
  return ['json', 'form'].includes(value) ? value : 'unknown';
}

/** The events the App is subscribed to. Pure. */
export function subscribedEvents(app) {
  const events = (app || {}).events;
  return Array.isArray(events) ? events.map((e) => String(e)) : [];
}

/** An ISO 8601 timestamp as a Date, or null. Pure. */
export function parseTime(text) {
  const raw = String(text ?? '').trim();
  if (!raw) return null;
  const moment = new Date(raw);
  return Number.isNaN(moment.getTime()) ? null : moment;
}

/** The most recent delivered_at in a delivery list, or null. Pure. */
export function lastDelivery(deliveries) {
  let latest = null;
  for (const record of deliveries || []) {
    if (!record || typeof record !== 'object') continue;
    const moment = parseTime(record.delivered_at);
    if (moment && (latest === null || moment > latest)) latest = moment;
  }
  return latest;
}

/** Whether anything has arrived recently. Corroboration, never proof. Pure. */
export function deliveryState(deliveries, now, staleDays = DEFAULT_STALE_DAYS) {
  if (deliveries === null || deliveries === undefined) return 'unknown';
  if (deliveries.length === 0) return 'none';
  const latest = lastDelivery(deliveries);
  if (latest === null || !now) return 'unknown';
  const days = Math.floor((now.getTime() - latest.getTime()) / 86400000);
  return days >= Number(staleDays) ? 'stale' : 'recent';
}

/** Turn the destination, the subscriptions and the log into a finding. Pure. */
export function verdict(url, events, deliveriesState) {
  const klass = urlClass(url);
  const count = (events || []).length;
  if (klass === 'unset') {
    if (count) {
      return ['no-url-subscribed',
        `the App subscribes to ${count} event(s) and has no webhook URL, so `
        + 'nothing is delivered and nothing fails. There is no log to read '
        + 'because there are no deliveries.'];
    }
    return ['no-url',
      'the App has no webhook URL and subscribes to no events. That is a '
      + 'coherent configuration for an App that only polls or creates its own '
      + 'repository hooks, so this is reported rather than judged.'];
  }
  if (klass === 'malformed') {
    return ['malformed-url',
      'the webhook URL is not a usable http or https URL, so no delivery can be '
      + 'attempted against it.'];
  }
  if (klass === 'placeholder') {
    return ['placeholder-url',
      'the webhook URL points at a placeholder host from a template. It looks '
      + 'configured and it reaches nothing you own.'];
  }
  if (klass === 'tunnel') {
    return ['tunnel-url',
      'the App delivers to a development proxy from the quickstart. Every event '
      + 'goes to a channel nobody is listening to, and the field looks filled in '
      + 'to anyone glancing at it.'];
  }
  if (klass === 'loopback') {
    return ['loopback-url',
      'the webhook URL is a loopback or link-local address, which GitHub cannot '
      + 'reach from the internet at all.'];
  }
  if (klass === 'insecure') {
    return ['insecure-url',
      'the App delivers over plain http, so payloads and signatures cross the '
      + 'network in the clear. Deliveries do arrive, which is why this survives so long.'];
  }
  if (deliveriesState === 'none' && count) {
    return ['no-deliveries',
      `the URL looks like a real destination and the App subscribes to ${count} `
      + 'event(s), but nothing has been delivered in the retained window. Either '
      + 'the events have genuinely not happened or the destination has never worked.'];
  }
  if (deliveriesState === 'stale' && count) {
    return ['silent',
      'the destination has delivered before and has gone quiet. That is a '
      + 'receiver or subscription question rather than a configuration one.'];
  }
  if (!count) {
    return ['no-events',
      'the webhook URL is a real destination but the App subscribes to no '
      + 'events, so nothing will ever be sent to it. That is a subscription '
      + 'finding, not a URL one.'];
  }
  return ['delivering',
    `the App has a real destination, subscribes to ${count} event(s), and events are arriving.`];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (['no-url-subscribed', 'placeholder-url', 'tunnel-url', 'loopback-url',
    'malformed-url'].includes(state)) {
    return "point the App's webhook at the production receiver, set a secret, "
      + 'set content_type to json, and then confirm with GET /app/hook/deliveries '
      + 'that events start arriving. The settings page will show a URL that '
      + 'nothing can reach.';
  }
  if (state === 'insecure-url') {
    return 'move the destination to https before anything else. The payload and '
      + 'its signature are readable in transit today.';
  }
  if (state === 'no-deliveries') {
    return 'check the receiver is reachable from the internet, then wait for an '
      + 'event you can cause on purpose and read the delivery log again. An empty '
      + 'log alone is not proof of anything.';
  }
  if (state === 'no-events') {
    return 'subscribe the App to the events it handles. The destination is fine '
      + 'and there is nothing being sent to it.';
  }
  if (state === 'no-url') {
    return 'nothing, if the App is meant to poll or manage its own repository '
      + 'hooks. If it is meant to react to events, this is the whole problem.';
  }
  if (state === 'silent') {
    return 'look at the receiver and the subscription list rather than the URL, '
      + 'which is working.';
  }
  return 'nothing.';
}

function headers(jwt) {
  return {
    Authorization: `Bearer ${jwt}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
}

async function get(jwt, path) {
  const res = await fetch(API + path, { headers: headers(jwt) });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return { status: res.status, body };
}

async function main() {
  const jwt = (process.env.GITHUB_APP_JWT || "dummy-github-app-jwt");
  if (!jwt) {
    console.error("set GITHUB_APP_JWT to a JWT signed with the App's private key");
    process.exitCode = 2;
    return;
  }
  const staleDays = Number((process.env.GITHUB_STALE_DAY || "dummy-github-stale-day")S || DEFAULT_STALE_DAYS);

  const app = await get(jwt, '/app');
  if (app.status !== 200 || !app.body) {
    console.error(`GET /app returned ${app.status}; the JWT is not being accepted as an App`);
    process.exitCode = 2;
    return;
  }
  const events = subscribedEvents(app.body);
  console.log(`app: ${app.body.slug}, ${app.body.installations_count} installation(s), `
    + `subscribed to ${events.length} event(s)`);

  const cfg = await get(jwt, '/app/hook/config');
  if (cfg.status !== 200 || !cfg.body) {
    console.error(`GET /app/hook/config returned ${cfg.status}`);
    process.exitCode = 2;
    return;
  }
  const url = cfg.body.url;
  console.log(`hook config: url=${url || '(empty)'} content_type=${contentTypeOf(cfg.body)} `
    + `secret=${secretState(cfg.body)}`);

  const dl = await get(jwt, '/app/hook/deliveries?per_page=100');
  const records = dl.status === 200 && Array.isArray(dl.body) ? dl.body : null;
  const now = new Date();
  const stateOfLog = deliveryState(records, now, staleDays);
  const latest = lastDelivery(records || []);
  console.log(`deliveries: ${records === null ? 'unreadable' : records.length} in the `
    + `retained window, most recent ${latest ? latest.toISOString().slice(0, 10) : 'none'}`);

  const [state, detail] = verdict(url, events, stateOfLog);
  console.log(`${state}: ${detail}`);
  console.log(`repair: ${repair(state)}`);
  console.log(JSON.stringify({
    app: app.body.slug,
    installations: app.body.installations_count,
    events,
    hook_url: url,
    url_class: urlClass(url),
    content_type: contentTypeOf(cfg.body),
    secret: secretState(cfg.body),
    deliveries_retained: records === null ? null : records.length,
    delivery_state: stateOfLog,
    state,
  }, null, 2));
  process.exitCode = ['no-url-subscribed', 'placeholder-url', 'tunnel-url',
    'loopback-url', 'malformed-url', 'insecure-url'].includes(state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
