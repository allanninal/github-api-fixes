/**
 * Decide whether a polling loop should be a webhook, and cost it if it should.
 *
 * Read only. Two GETs to list hooks, one to read the quota, and the repair is
 * printed as a command rather than run.
 *
 * How often a client polls is not visible from the API. Whether a push path
 * exists at all is, and that is the half worth checking.
 */
const API = 'https://api.github.com';
const UA = 'github-webhook-vs-poll/1.0';
export const HOURLY_LIMIT = 5000;

// The polled endpoint on the left, the event that would push the same thing on
// the right. Anything not on this list is a real reason to keep polling.
export const CONCERNS = {
  issues: ['GET /repos/{owner}/{repo}/issues', ['issues']],
  issue_comments: ['GET /repos/{owner}/{repo}/issues/comments', ['issue_comment']],
  pulls: ['GET /repos/{owner}/{repo}/pulls', ['pull_request']],
  commits: ['GET /repos/{owner}/{repo}/commits', ['push']],
  releases: ['GET /repos/{owner}/{repo}/releases', ['release']],
  workflow_runs: ['GET /repos/{owner}/{repo}/actions/runs', ['workflow_run']],
};

/**
 * Split hook subscriptions into what delivers and what does not. Pure.
 * Inactive hooks are kept separately: "there is a hook and it is switched off"
 * is a much faster fix than "there is no hook".
 */
export function subscribedEvents(hooks) {
  const active = new Set();
  const inactive = new Set();
  let wildcard = false;
  let inactiveWildcard = false;
  for (const hook of hooks ?? []) {
    if (!hook || typeof hook !== 'object') continue;
    const live = hook.active !== false;
    for (const event of hook.events ?? []) {
      const name = String(event);
      if (live) {
        active.add(name);
        wildcard = wildcard || name === '*';
      } else {
        inactive.add(name);
        inactiveWildcard = inactiveWildcard || name === '*';
      }
    }
  }
  return { events: active, wildcard, inactive, inactive_wildcard: inactiveWildcard };
}

/** One row per polled concern saying whether anything would push it. Pure. */
export function coverage(concerns, hooks) {
  const subs = subscribedEvents(hooks);
  const rows = [];
  for (const concern of concerns ?? []) {
    const wanted = (CONCERNS[concern] ?? [null, [concern]])[1];
    const names = wanted.join('/');
    if (subs.wildcard) {
      rows.push({ concern, state: 'covered',
        detail: `a wildcard subscription delivers ${names}, though it delivers everything else too` });
    } else if (wanted.some((w) => subs.events.has(w))) {
      rows.push({ concern, state: 'covered', detail: `an active hook subscribes to ${names}` });
    } else if (wanted.some((w) => subs.inactive.has(w)) || subs.inactive_wildcard) {
      rows.push({ concern, state: 'uncovered',
        detail: `a hook subscribes to ${names} but it is not active, and an inactive hook delivers nothing` });
    } else {
      rows.push({ concern, state: 'uncovered', detail: `no hook subscribes to ${names}` });
    }
  }
  return rows;
}

/** Requests and detection latency for the loop as configured. Pure. */
export function pollCost(concerns, intervalS, repos = 1) {
  const n = Math.max(0, Number.parseInt(repos, 10) || 0);
  const interval = Math.max(1, Number.parseInt(intervalS, 10) || 1);
  const calls = (concerns ?? []).length * n;
  const perHour = Math.round((calls * 3600) / interval);
  return {
    requests_per_hour: perHour,
    requests_per_day: perHour * 24,
    mean_latency_s: interval / 2,
    worst_latency_s: interval,
  };
}

/** Turn coverage and cost into one finding. Pure. */
export function verdict(rows, cost, hourlyLimit = HOURLY_LIMIT) {
  if (!rows || !rows.length) {
    return ['nothing-polled',
      'no concerns were named, so there is nothing to compare against the hooks'];
  }
  const uncovered = rows.filter((r) => r.state === 'uncovered');
  const share = (cost.requests_per_hour ?? 0) / Math.max(1, hourlyLimit);

  if (!uncovered.length) {
    return ['push',
      'every polled concern already has an active hook, so this loop is a ' +
      'reconciliation pass rather than a detection mechanism. Run it on a slow schedule.'];
  }
  const summary = `${uncovered.length} of ${rows.length} polled concern(s) have ` +
    `no active hook. The loop costs ${cost.requests_per_hour ?? 0} request(s) an ` +
    `hour to notice them ${Math.round(cost.mean_latency_s ?? 0)}s late on average.`;
  if (share >= 0.5) {
    return ['polling-dominates',
      `${summary} That is ${Math.round(share * 100)}% of the hourly quota spent ` +
      'on the clock rather than on activity.'];
  }
  return ['polling', summary];
}

async function get(token, path) {
  const url = path.startsWith('/') ? API + path : path;
  const res = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': UA,
    },
  });
  let body = null;
  try { body = await res.json(); } catch { body = null; }
  return { status: res.status, body };
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  if (!token) {
    console.error('set GITHUB_TOKEN. Listing hooks needs admin on the ' +
      'repository, but only read access to it');
    process.exitCode = 2;
    return;
  }
  const repo = process.argv[2];
  if (!repo) {
    console.error('usage: node github-webhook-vs-poll.mjs owner/name [concerns] [interval] [repos] [org]');
    process.exitCode = 2;
    return;
  }
  const concerns = (process.argv[3] ?? 'issues,issue_comments,pulls')
    .split(',').map((c) => c.trim()).filter(Boolean);
  const interval = Number.parseInt(process.argv[4] ?? '30', 10) || 30;
  const repos = Number.parseInt(process.argv[5] ?? '1', 10) || 1;
  const org = process.argv[6];

  const hooks = [];
  const blind = [];
  const repoHooks = await get(token, `/repos/${repo}/hooks`);
  if (repoHooks.status === 200 && Array.isArray(repoHooks.body)) {
    hooks.push(...repoHooks.body);
    console.log(`${repo}: ${repoHooks.body.length} repository hook(s)`);
  } else {
    blind.push(`repository hooks (${repoHooks.status})`);
    console.warn(`could not read repository hooks: ${repoHooks.status}. This ` +
      'token cannot see them, which is not the same as there being none.');
  }

  if (org) {
    const orgHooks = await get(token, `/orgs/${org}/hooks`);
    if (orgHooks.status === 200 && Array.isArray(orgHooks.body)) {
      hooks.push(...orgHooks.body);
      console.log(`${org}: ${orgHooks.body.length} organisation hook(s)`);
    } else {
      blind.push(`organisation hooks (${orgHooks.status})`);
      console.warn(`could not read organisation hooks: ${orgHooks.status}`);
    }
  }

  for (const hook of hooks) {
    console.log(`  hook ${hook.id} active=${hook.active} events=` +
      `${(hook.events ?? []).join(',') || 'none'}`);
  }

  const rows = coverage(concerns, hooks);
  const cost = pollCost(concerns, interval, repos);
  for (const row of rows) console.log(`${row.concern} ${row.state} ${row.detail}`);

  const rate = await get(token, '/rate_limit');
  if (rate.status === 200) {
    const core = ((rate.body ?? {}).resources ?? {}).core ?? {};
    console.log(`core quota: ${core.used} used of ${core.limit}`);
  }

  const [state, detail] = verdict(rows, cost);
  console.log(`${state}: ${detail}`);
  if (blind.length) {
    console.warn(`unread: ${blind.join('; ')}. Anything reported as uncovered ` +
      'may already be covered by a hook this token cannot see.');
  }

  if (state === 'polling' || state === 'polling-dominates') {
    const needed = [...new Set(rows.filter((r) => r.state === 'uncovered')
      .flatMap((r) => (CONCERNS[r.concern] ?? [null, [r.concern]])[1]))].sort();
    console.log('repair: create one hook for the events you consume. This ' +
      'script does not create it:');
    console.log(`  gh api --method POST /repos/${repo}/hooks -f name=web ` +
      '-f config[url]=https://example.test/hooks -f config[content_type]=json ' +
      `-f config[secret]=YOURSECRET ${needed.map((e) => `-f events[]=${e}`).join(' ')}`);
    console.log(`repair: keep the poll as reconciliation at a much longer ` +
      `interval, an hour rather than ${interval}s.`);
  }

  console.log(JSON.stringify({ rows, cost, state, hooks: hooks.length, unread: blind }, null, 2));
  process.exitCode = (state === 'polling' || state === 'polling-dominates') ? 1 : 0;
}

// Only run when invoked directly, so importing this module from the test file
// does not execute main() and fail on the missing token.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
