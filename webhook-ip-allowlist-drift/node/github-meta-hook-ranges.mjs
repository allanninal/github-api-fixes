/**
 * Compare GitHub's published webhook source ranges against your allow-list.
 *
 * Read only, and unauthenticated: GET /meta needs no token, so the person who
 * owns the firewall can run this without being issued a GitHub credential.
 *
 * The address arithmetic is written out here because there is no address type
 * in the standard library and GitHub publishes IPv6 ranges as well as IPv4.
 *
 * Usage:
 *   node github-meta-hook-ranges.mjs ./firewall-github.txt
 */
import { readFile } from 'node:fs/promises';

const META = 'https://api.github.com/meta';
const UA = 'github-meta-hook-ranges/1.0';

export const HOOKS = 'hooks';
export const OTHER_ARRAYS = ['api', 'web', 'git', 'packages', 'actions', 'dependabot'];
export const WRONG_ARRAY_MARGIN = 0.5;

function isDigits(text, maxLength) {
  if (typeof text !== 'string' || text.length === 0 || text.length > maxLength) return false;
  for (const ch of text) if (ch < '0' || ch > '9') return false;
  return true;
}

function ipv4ToBig(addr) {
  const parts = String(addr).split('.');
  if (parts.length !== 4) return null;
  let n = 0n;
  for (const part of parts) {
    if (!isDigits(part, 3)) return null;
    const value = Number(part);
    if (value > 255) return null;
    n = (n << 8n) | BigInt(value);
  }
  return n;
}

function ipv6ToBig(addr) {
  let text = String(addr).toLowerCase();
  if (text.includes('.')) {
    // An embedded IPv4 tail, as in ::ffff:192.0.2.1.
    const cut = text.lastIndexOf(':');
    const tail = ipv4ToBig(text.slice(cut + 1));
    if (tail === null) return null;
    text = `${text.slice(0, cut + 1)}${(tail >> 16n).toString(16)}:${(tail & 0xffffn).toString(16)}`;
  }
  const halves = text.split('::');
  if (halves.length > 2) return null;
  const head = halves[0] ? halves[0].split(':') : [];
  const tail = halves.length === 2 && halves[1] ? halves[1].split(':') : [];
  if (halves.length === 1 && head.length !== 8) return null;
  if (head.length + tail.length > 8) return null;
  const filler = new Array(8 - head.length - tail.length).fill('0');
  const groups = halves.length === 1 ? head : [...head, ...filler, ...tail];
  let n = 0n;
  for (const group of groups) {
    if (group.length === 0 || group.length > 4) return null;
    for (const ch of group) if (!'0123456789abcdef'.includes(ch)) return null;
    n = (n << 16n) | BigInt(parseInt(group, 16));
  }
  return n;
}

/** {version, start, end} for one entry, or null. Host bits tolerated. Pure. */
export function parseCidr(text) {
  const raw = String(text ?? '').split('#')[0].trim();
  if (!raw) return null;
  const slash = raw.indexOf('/');
  const addr = slash === -1 ? raw : raw.slice(0, slash);
  const prefixText = slash === -1 ? null : raw.slice(slash + 1);
  const version = addr.includes(':') ? 6 : 4;
  const bits = version === 4 ? 32 : 128;
  const base = version === 4 ? ipv4ToBig(addr) : ipv6ToBig(addr);
  if (base === null) return null;
  let prefix = bits;
  if (prefixText !== null) {
    if (!isDigits(prefixText, 3)) return null;
    prefix = Number(prefixText);
    if (prefix > bits) return null;
  }
  const hostBits = BigInt(bits - prefix);
  const start = (base >> hostBits) << hostBits;
  return { version, start, end: start + (1n << hostBits) - 1n };
}

/** [ranges, unreadable] from an exported rule list. Pure. */
export function readAllowlist(lines) {
  const ranges = [];
  const unreadable = [];
  for (const line of lines || []) {
    const text = String(line).split('#')[0].trim();
    if (!text) continue;
    const parsed = parseCidr(text);
    if (parsed === null) unreadable.push(text);
    else ranges.push(parsed);
  }
  return [ranges, unreadable];
}

/** How many addresses a parsed range holds. Pure. */
export function sizeOf(range) {
  return range.end - range.start + 1n;
}

/** The addresses two ranges share, or null. Pure. */
export function overlap(a, b) {
  if (a.version !== b.version) return null;
  const start = a.start > b.start ? a.start : b.start;
  const end = a.end < b.end ? a.end : b.end;
  return start <= end ? { version: a.version, start, end } : null;
}

/** Merge overlapping and adjacent intervals so nothing is counted twice. Pure. */
export function merge(intervals) {
  const sorted = [...intervals].sort((a, b) => {
    if (a.start < b.start) return -1;
    if (a.start > b.start) return 1;
    return 0;
  });
  const out = [];
  for (const piece of sorted) {
    const last = out[out.length - 1];
    if (last && piece.start <= last.end + 1n) {
      last.end = piece.end > last.end ? piece.end : last.end;
    } else {
      out.push({ version: piece.version, start: piece.start, end: piece.end });
    }
  }
  return out;
}

/** How many addresses of one published range the allow-list permits. Pure. */
export function coveredAddresses(published, allowed) {
  const pieces = (allowed || []).map((a) => overlap(published, a)).filter(Boolean);
  return merge(pieces).reduce((total, piece) => total + (piece.end - piece.start + 1n), 0n);
}

/** [state, fraction] for one published range, measured over addresses. Pure. */
export function coverage(published, allowed) {
  const total = sizeOf(published);
  const covered = coveredAddresses(published, allowed);
  if (covered <= 0n) return ['none', 0];
  if (covered >= total) return ['full', 1];
  return ['partial', Number((covered * 10000n) / total) / 10000];
}

/** Whether a default route makes the allow-list decorative. Pure. */
export function allowsEverything(allowed) {
  for (const range of allowed || []) {
    const bits = range.version === 4 ? 32n : 128n;
    if (range.start === 0n && range.end === (1n << bits) - 1n) return true;
  }
  return false;
}

/** [[cidr, state, fraction]] for every published range. Pure. */
export function audit(publishedCidrs, allowed) {
  const rows = [];
  for (const cidr of publishedCidrs || []) {
    const parsed = parseCidr(cidr);
    if (parsed === null) {
      rows.push([String(cidr), 'unreadable', 0]);
      continue;
    }
    const [state, fraction] = coverage(parsed, allowed);
    rows.push([String(cidr), state, fraction]);
  }
  return rows;
}

/** The published ranges that are not fully covered. Pure. */
export function uncovered(rows) {
  return (rows || []).filter(([, state]) => state !== 'full').map(([cidr]) => cidr);
}

/** Mean coverage of one /meta array by the allow-list, 0 to 1. Pure. */
export function arrayScore(meta, allowed, key) {
  const values = (meta || {})[key];
  if (!Array.isArray(values) || values.length === 0) return 0;
  const rows = audit(values, allowed);
  return rows.reduce((sum, [, , fraction]) => sum + fraction, 0) / rows.length;
}

/** [key, score] for the non-hooks array the allow-list matches best. Pure. */
export function bestOtherArray(meta, allowed) {
  let best = null;
  let score = 0;
  for (const key of OTHER_ARRAYS) {
    const value = arrayScore(meta, allowed, key);
    if (value > score) { best = key; score = value; }
  }
  return [best, score];
}

/** Turn the comparison into a finding. Pure. */
export function verdict(meta, allowed, unreadable = 0) {
  const published = (meta || {})[HOOKS];
  if (!Array.isArray(published) || published.length === 0) {
    return ['no-hooks-array',
      'GET /meta did not return a hooks array. Nothing can be compared until it does.'];
  }
  if (!allowed || allowed.length === 0) {
    return ['no-allowlist',
      'the allow-list is empty, so either nothing is permitted or the export is '
      + 'wrong. Check the export before reading anything else here.'];
  }
  if (allowsEverything(allowed)) {
    return ['allow-all',
      'the allow-list contains a default route, so every published range is '
      + 'covered and the control is not filtering anything. This audit will pass '
      + 'forever and mean nothing.'];
  }
  const rows = audit(published, allowed);
  const missing = uncovered(rows);
  const hooksScore = arrayScore(meta, allowed, HOOKS);
  const [other, otherScore] = bestOtherArray(meta, allowed);
  if (missing.length && other && otherScore > hooksScore + WRONG_ARRAY_MARGIN) {
    return ['wrong-array',
      `the allow-list covers the ${other} ranges ${Math.round(otherScore * 100)}% `
      + `and the hooks ranges ${Math.round(hooksScore * 100)}%. This list was built `
      + `from the wrong section of GET /meta: ${other} is inbound traffic, and `
      + 'webhooks arrive from hooks.'];
  }
  if (missing.length) {
    return ['drifted',
      `${missing.length} of ${rows.length} published hook ranges are not fully `
      + 'covered by the allow-list. Partial coverage fails intermittently, which '
      + 'is why this reads as flakiness rather than as a blocked range.'];
  }
  if (unreadable) {
    return ['current-with-gaps',
      `every published hook range is covered, but ${unreadable} allow-list entries `
      + 'could not be parsed and were left out of the audit.'];
  }
  return ['current', 'every published hook range is fully covered by the allow-list.'];
}

/** The sentence a reader has to act on. Pure. */
export function repair(state) {
  if (['drifted', 'current-with-gaps'].includes(state)) {
    return 'generate the allow-list from GET /meta on a schedule rather than '
      + 'maintaining it by hand, and alert when the published set changes so the '
      + 'next change is a pull request instead of an incident. The current set is '
      + 'printed below in full.';
  }
  if (state === 'wrong-array') {
    return 'rebuild the allow-list from the hooks array. The array in use is '
      + 'where GitHub serves traffic you connect to, not where webhook '
      + 'deliveries come from.';
  }
  if (state === 'allow-all') {
    return 'remove the default route or accept that this control does nothing. '
      + 'Either way, verify X-Hub-Signature-256 on every request: the signature '
      + 'is what authenticates an event, and an IP list never was.';
  }
  if (state === 'no-allowlist') {
    return 'export the rules from the device or the infrastructure code that '
      + 'defines them, one CIDR per line, and run this again.';
  }
  if (state === 'current') {
    return 'nothing today. Put this on a schedule so the answer stays true, and '
      + 'keep signature verification as the real control.';
  }
  return 'nothing.';
}

async function main() {
  const path = process.argv[2] || (process.env.GITHUB_ALLOWLIST || "dummy-github-allowlist");
  if (!path) {
    console.error('usage: node github-meta-hook-ranges.mjs ./firewall-github.txt');
    process.exitCode = 2;
    return;
  }
  const text = await readFile(path, 'utf8');
  const [allowed, unreadable] = readAllowlist(text.split(String.fromCharCode(10)));
  for (const line of unreadable) {
    console.error(`allow-list entry not understood, left out of the audit: ${line}`);
  }

  const res = await fetch(META, {
    headers: { Accept: 'application/vnd.github+json', 'User-Agent': UA },
  });
  if (res.status !== 200) {
    console.error(`GET /meta returned ${res.status}`);
    process.exitCode = 2;
    return;
  }
  const meta = await res.json();
  const published = meta[HOOKS] || [];
  console.log(`GET /meta: ${published.length} hooks range(s) published, `
    + `allow-list holds ${allowed.length} entry/entries`);
  const rows = audit(published, allowed);
  for (const [cidr, state, fraction] of rows) {
    console.log(`${cidr.padEnd(22)} ${state.padEnd(9)} ${Math.round(fraction * 100)}% covered`);
  }
  const [state, detail] = verdict(meta, allowed, unreadable.length);
  console.log(`${state}: ${detail}`);
  console.log(`repair: ${repair(state)}`);
  if (['drifted', 'wrong-array', 'current-with-gaps'].includes(state)) {
    console.log('the published hooks ranges, in full:');
    for (const cidr of published) console.log(`  ${cidr}`);
  }
  console.log(JSON.stringify({
    published_hooks_ranges: published,
    allowlist_entries: allowed.length,
    allowlist_unreadable: unreadable,
    not_fully_covered: uncovered(rows),
    hooks_score: Number(arrayScore(meta, allowed, HOOKS).toFixed(4)),
    best_other_array: bestOtherArray(meta, allowed)[0],
    state,
  }, null, 2));
  process.exitCode = ['drifted', 'wrong-array', 'allow-all', 'no-allowlist']
    .includes(state) ? 1 : 0;
}

// Guarded so importing this file from the test runner does not start a run and
// fail the suite even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
