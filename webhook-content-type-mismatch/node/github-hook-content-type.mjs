/**
 * Say whether a webhook sends a body encoding its receiver cannot read.
 *
 * Read only. Three kinds of GET: the hook list, the delivery list, and a few
 * individual delivery records, which are the only place the request headers and
 * the recorded body appear. Nothing is created, edited or redelivered.
 *
 * Environment:
 *   GITHUB_TOKEN             a read-only token with access to the repository
 *   GITHUB_REPO              owner/name
 *   GITHUB_RECEIVER_PARSES   json or form, declared rather than read
 */
const API = 'https://api.github.com';
const UA = 'github-hook-content-type/1.0';

export const FORM = 'form';
export const JSON_CT = 'json';
/** The documented default: an absent key means form. */
export const DEFAULT_CONTENT_TYPE = FORM;
/** Corroboration only; a tolerant framework answers 200 and this stays empty. */
export const PARSE_STATUSES = [400, 415, 422];

/** The hook's configured body encoding, normalised. Pure. */
export function contentTypeOf(config) {
  if (!config || typeof config !== 'object') return 'unknown';
  const raw = config.content_type;
  if (raw === null || raw === undefined) return DEFAULT_CONTENT_TYPE;
  const value = String(raw).trim().toLowerCase();
  if (['json', 'application/json'].includes(value)) return JSON_CT;
  if (['form', 'application/x-www-form-urlencoded'].includes(value)) return FORM;
  return 'unknown';
}

/** Whether the hook names its encoding or inherits the default. Pure. */
export function contentTypeWasExplicit(config) {
  return Boolean(config && typeof config === 'object'
    && config.content_type !== null && config.content_type !== undefined);
}

/** One header from a delivery record, case-insensitively. Pure. */
export function headerOf(headers, name) {
  if (!headers || typeof headers !== 'object') return null;
  const wanted = String(name).trim().toLowerCase();
  for (const [key, value] of Object.entries(headers)) {
    if (String(key).trim().toLowerCase() === wanted) return value;
  }
  return null;
}

/** Classify a content-type header value, ignoring parameters. Pure. */
export function encodingOfHeader(value) {
  if (value === null || value === undefined) return 'unknown';
  const text = String(value).split(';')[0].trim().toLowerCase();
  if (text === 'application/json') return JSON_CT;
  if (text === 'application/x-www-form-urlencoded') return FORM;
  return 'unknown';
}

/** What GitHub said it was sending on one delivery record. Pure. */
export function deliveryEncoding(delivery) {
  if (!delivery || typeof delivery !== 'object') return 'unknown';
  const request = delivery.request;
  if (!request || typeof request !== 'object') return 'unknown';
  return encodingOfHeader(headerOf(request.headers, 'content-type'));
}

/** Whether a recorded body is the payload= wrapper rather than the event. Pure. */
export function isFormWrapped(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return false;
  const keys = Object.keys(payload);
  return keys.length === 1 && keys[0] === 'payload' && typeof payload.payload === 'string';
}

/** Count the delivery records showing the form wrapper, both ways. Pure. */
export function wrapperEvidence(details) {
  const records = (details || []).filter((d) => d && typeof d === 'object');
  const formHeader = records.filter((d) => deliveryEncoding(d) === FORM).length;
  const formWrapper = records.filter((d) => isFormWrapped((d.request || {}).payload)).length;
  return { sampled: records.length, form_header: formHeader, form_wrapper: formWrapper };
}

/** How many recent attempts came back with a body-parse status. Pure. */
export function parseFailures(deliveries) {
  const records = (deliveries || []).filter((d) => d && typeof d === 'object');
  let hits = 0;
  for (const d of records) {
    const code = Number(d.status_code);
    if (Number.isFinite(code) && PARSE_STATUSES.includes(code)) hits += 1;
  }
  return [hits, records.length];
}

/** Normalise what the caller says the receiver parses. Pure. */
export function receiverOf(declared) {
  const value = String(declared ?? 'unknown').trim().toLowerCase();
  return [JSON_CT, FORM].includes(value) ? value : 'unknown';
}

/** Turn the configured encoding and the declared receiver into a finding. Pure. */
export function verdict(hookEncoding, declared, evidence = null, failures = 0, sampledTotal = 0) {
  const seen = evidence || {};
  const confirmed = Math.max(Number(seen.form_header || 0), Number(seen.form_wrapper || 0));
  const parsed = receiverOf(declared);
  let corroboration = '';
  if (confirmed) {
    corroboration = ` ${confirmed} of ${seen.sampled || 0} sampled deliveries carry the form encoding.`;
  }
  if (failures) {
    corroboration += ` ${failures} of ${sampledTotal} recent attempts came back 400, 415 or 422.`;
  }
  if (hookEncoding === 'unknown') {
    return ['encoding-unknown',
      'config.content_type holds a value this script does not recognise. GitHub '
      + 'supports json and form; anything else needs reading by hand before the '
      + 'rest of this is meaningful.'];
  }
  if (hookEncoding === FORM && parsed === JSON_CT) {
    return ['form-to-json',
      'the hook sends application/x-www-form-urlencoded and the receiver was '
      + 'declared as JSON. Every event arrives wrapped in a payload= field, so no '
      + 'key your handler reads exists at the top level of the body.' + corroboration];
  }
  if (hookEncoding === JSON_CT && parsed === FORM) {
    return ['json-to-form',
      'the hook sends application/json and the receiver was declared as a form '
      + 'parser. The body has no payload= field to unwrap, so the parsed result '
      + 'is empty rather than wrong.'];
  }
  if (hookEncoding === FORM && parsed === 'unknown') {
    return ['receiver-undeclared',
      'the hook sends application/x-www-form-urlencoded, which is the default '
      + 'rather than a decision. No receiver was declared, so this is a risk '
      + 'rather than a finding: confirm the handler unwraps the payload field '
      + 'before treating it as healthy.' + corroboration];
  }
  if (hookEncoding === FORM) {
    return ['consistent-form',
      'the hook sends application/x-www-form-urlencoded and the receiver was '
      + 'declared as a form parser. Consistent, but the signature covers the '
      + 'urlencoded wrapper, so verify over the raw bytes rather than over '
      + 'anything you unwrapped.'];
  }
  return ['consistent-json', 'the hook sends application/json and the receiver parses JSON.'];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (state === 'form-to-json') {
    return 'set config.content_type to json on the hook, and in the same change '
      + 'make signature verification read the raw request bytes before parsing. '
      + 'Then redeliver one event from the delivery log and confirm the handler ran.';
  }
  if (state === 'json-to-form') {
    return 'parse the body as JSON in the receiver. Changing the hook back to '
      + 'form to suit the parser is the wrong direction: form is the legacy '
      + 'encoding and it makes signature verification harder.';
  }
  if (state === 'receiver-undeclared') {
    return 'run this again with the receiver declared from the handler code. If '
      + 'the handler reads the body as JSON, this is a live finding; if it '
      + 'unwraps the payload field first, it is working as built.';
  }
  if (state === 'consistent-form') {
    return 'nothing urgent. Moving to json is still worth doing, because it '
      + 'removes a layer of encoding between the signature and the document you verify.';
  }
  if (state === 'encoding-unknown') {
    return 'read config.content_type by hand. Only json and form are supported '
      + 'values and neither of them is what is set here.';
  }
  return 'nothing.';
}

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
  };
}

async function get(token, path) {
  const res = await fetch(API + path, { headers: headers(token) });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return { status: res.status, body };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const repo = (process.env.GITHUB_REPO || "dummy-github-repo");
  if (!token || !repo) {
    console.error('set GITHUB_TOKEN and GITHUB_REPO=owner/name');
    process.exitCode = 2;
    return;
  }
  const declared = (process.env.GITHUB_RECEIVER_PARSE || "dummy-github-receiver-parse")S || null;
  const sample = Number((process.env.GITHUB_SAMPL || "dummy-github-sampl")E || 5);

  const list = await get(token, `/repos/${repo}/hooks?per_page=100`);
  if (list.status !== 200 || !Array.isArray(list.body)) {
    console.error(`GET /repos/${repo}/hooks returned ${list.status}`);
    process.exitCode = 2;
    return;
  }

  const report = [];
  let findings = 0;
  for (const hook of list.body) {
    const config = hook.config || {};
    const encoding = contentTypeOf(config);
    console.log(`hook ${hook.id} ${config.url} content_type=${encoding} `
      + `(${contentTypeWasExplicit(config) ? 'explicit' : 'default, key absent'})`);

    const dl = await get(token, `/repos/${repo}/hooks/${hook.id}/deliveries?per_page=100`);
    const deliveries = dl.status === 200 && Array.isArray(dl.body) ? dl.body : [];
    const details = [];
    for (const d of deliveries.slice(0, sample)) {
      const one = await get(token, `/repos/${repo}/hooks/${hook.id}/deliveries/${d.id}`);
      if (one.status === 200 && one.body) details.push(one.body);
    }
    const evidence = wrapperEvidence(details);
    const [failures, total] = parseFailures(deliveries);
    console.log(`deliveries sampled: ${evidence.sampled}, form content-type header on `
      + `${evidence.form_header}, payload= wrapper on ${evidence.form_wrapper}`);
    console.log(`parse statuses (400/415/422): ${failures} of ${total} recent deliveries`);

    const [state, detail] = verdict(encoding, declared, evidence, failures, total);
    console.log(`${state}: ${detail}`);
    console.log(`repair: ${repair(state)}`);
    if (['form-to-json', 'json-to-form', 'encoding-unknown'].includes(state)) findings += 1;
    report.push({
      hook_id: hook.id,
      url: config.url,
      content_type: encoding,
      content_type_explicit: contentTypeWasExplicit(config),
      receiver_declared: receiverOf(declared),
      sampled: evidence.sampled,
      parse_status_count: failures,
      deliveries_examined: total,
      state,
    });
  }
  console.log(JSON.stringify({ repository: repo, hooks: report }, null, 2));
  process.exitCode = findings ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
