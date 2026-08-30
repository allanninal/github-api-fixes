/**
 * Measure what conditional requests would save against the GitHub rate limit.
 *
 * Read only. Two GETs against one endpoint: the second sends If-None-Match with
 * the ETag the first returned. A 304 Not Modified does not count against the
 * primary rate limit, and x-ratelimit-used on both responses proves it.
 */
const API = 'https://api.github.com';
const UA = 'github-etag-saving/1.0';

export const DEFAULT_LIMIT = 5000;

/**
 * Compare a plain response with the conditional one that followed. Pure.
 * Each argument is { status, etag, used }. Returns [state, report].
 */
export function measure(first, second) {
  const etag = first?.etag ?? null;
  const before = first?.used;
  const after = second?.used;
  const status = second?.status;

  const parsedBefore = Number.parseInt(before, 10);
  const parsedAfter = Number.parseInt(after, 10);
  const delta = (Number.isFinite(parsedBefore) && Number.isFinite(parsedAfter))
    ? parsedAfter - parsedBefore : null;

  const report = {
    etag,
    used_before: before ?? null,
    used_after: after ?? null,
    cost_of_unchanged_poll: delta,
    first_status: first?.status ?? null,
    second_status: status ?? null,
  };

  if (!etag) return ['no-etag', report];
  if (status !== 304) return ['not-honoured', report];
  if (delta === null) return ['unmeasured', report];
  if (delta > 0) return ['billed', report];
  return ['free', report];
}

/**
 * Price a polling schedule with and without conditional requests. Pure.
 * unchangedFraction is how much of what you poll is typically unchanged.
 */
export function project(pollSeconds, endpoints, limit = DEFAULT_LIMIT, unchangedFraction = 1) {
  const seconds = Math.max(1, Number(pollSeconds));
  const count = Math.max(1, Math.trunc(endpoints));
  const cap = Math.max(1, Math.trunc(limit));
  const fraction = Math.min(1, Math.max(0, Number(unchangedFraction)));

  const without = (3600 / seconds) * count;
  const withEtags = without * (1 - fraction);
  const round = (n) => Math.round(n * 10) / 10;
  return {
    per_hour_without: round(without),
    per_hour_with: round(withEtags),
    saved_per_hour: round(without - withEtags),
    percent_without: round(100 * without / cap),
    percent_with: round(100 * withEtags / cap),
    limit: cap,
  };
}

/** Turn the measurement and the projection into one line. Pure. */
export function verdict(state, projection) {
  const saved = projection?.saved_per_hour ?? 0;
  const percent = projection?.percent_without ?? 0;

  if (state === 'no-etag') {
    return ['unavailable',
      'the response carried no etag, so this endpoint cannot be polled ' +
      'conditionally. Check last-modified and use if-modified-since where it ' +
      'is present.'];
  }
  if (state === 'not-honoured') {
    return ['ignored',
      'the conditional request came back 200 rather than 304. Either the ' +
      'resource genuinely changed between the two calls, or something between ' +
      'this client and GitHub is dropping the If-None-Match header, which ' +
      'silently reinstates the full cost.'];
  }
  if (state === 'billed') {
    return ['billed',
      'the 304 arrived and x-ratelimit-used still moved, which is not how ' +
      'conditional requests are documented to behave. Re-run before acting on ' +
      'it: another process sharing this token spends the same counter.'];
  }
  if (state === 'unmeasured') {
    return ['unmeasured',
      'the 304 arrived but x-ratelimit-used was missing from one of the ' +
      'responses, so the saving is real and its size is not measured here.'];
  }
  return [percent < 25 ? 'saving' : 'large-saving',
    `the 304 cost 0 request(s). At this poll rate that is ${Math.round(saved)} ` +
    `request(s) an hour, ${percent}% of the quota, currently spent on data that ` +
    'did not change.'];
}

function read(res) {
  const headers = {};
  for (const [k, v] of res.headers.entries()) headers[k.toLowerCase()] = v;
  const used = Number.parseInt(headers['x-ratelimit-used'], 10);
  return {
    status: res.status,
    etag: headers.etag ?? null,
    used: Number.isFinite(used) ? used : null,
    last_modified: headers['last-modified'] ?? null,
    limit: headers['x-ratelimit-limit'] ?? null,
  };
}

function head(token, extra = {}) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': UA,
    ...extra,
  };
}

async function main() {
  const repo = process.argv[2];
  const path = process.argv[3] ?? '/issues';
  const pollSeconds = Number.parseFloat(process.argv[4] ?? '60') || 60;
  const endpoints = Number.parseInt(process.argv[5] ?? '1', 10) || 1;
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");

  if (!token) {
    console.error('set GITHUB_TOKEN (a read-only token is enough)');
    process.exitCode = 2;
    return;
  }
  if (!repo || !repo.includes('/')) {
    console.error('usage: node github-etag-saving.mjs owner/name [/path] ' +
      '[pollSeconds] [endpoints]');
    process.exitCode = 2;
    return;
  }

  const url = `${API}/repos/${repo}${path}`;
  console.log(`probing ${url} twice: once plain, once with If-None-Match`);

  const plain = await fetch(url, { headers: head(token) });
  if (plain.status === 401) {
    console.error('401 from GitHub: GITHUB_TOKEN is missing, expired or malformed');
    process.exitCode = 2;
    return;
  }
  if (plain.status === 403 || plain.status === 404) {
    console.error(`${plain.status} from ${url}: this token cannot read that ` +
      'endpoint. GitHub answers 404 rather than 403 when a token cannot see a ' +
      'resource at all.');
    process.exitCode = 2;
    return;
  }
  const first = read(plain);
  console.log(`  plain:       ${first.status}, etag ${first.etag}, ` +
    `x-ratelimit-used ${first.used}`);

  let second = first;
  if (first.etag) {
    const conditional = await fetch(url, {
      headers: head(token, { 'If-None-Match': first.etag }),
    });
    second = read(conditional);
    console.log(`  conditional: ${second.status}, x-ratelimit-used ${second.used}`);
  } else if (first.last_modified) {
    console.warn(`  no etag, but last-modified is ${first.last_modified}: use ` +
      'if-modified-since on this endpoint instead');
  }

  const [state, report] = measure(first, second);
  const limit = Number.parseInt(first.limit, 10) || DEFAULT_LIMIT;
  const projection = project(pollSeconds, endpoints, limit, 1);
  const [level, detail] = verdict(state, projection);
  console.log(`${level}: ${detail}`);
  console.log(`  ${projection.per_hour_without} request(s)/hour now ` +
    `(${projection.percent_without}% of ${projection.limit}), ` +
    `${projection.per_hour_with}/hour with conditional requests ` +
    `(${projection.percent_with}%)`);

  if (level === 'saving' || level === 'large-saving') {
    console.log(`  repair: store ${report.etag} against this exact URL and ` +
      "credential, send it back as If-None-Match, and treat 304 as 'keep what " +
      "you have' rather than as an error.");
    console.log('  repair: keep per_page, sort and Accept stable, and key the ' +
      'cache by token: an ETag is scoped to the credential that fetched it, so ' +
      'a rotation invalidates every entry at once.');
  }
  process.exitCode = ['saving', 'large-saving', 'unavailable'].includes(level) ? 0 : 1;
}

// Only run when invoked directly. The test file imports this module, and without
// the guard main() would run there too, fail on the missing token, and set a
// non-zero exit code that fails the whole test file even as every test passes.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
