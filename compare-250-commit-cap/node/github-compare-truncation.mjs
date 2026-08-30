/**
 * Report whether a compare response was silently truncated at 250 commits.
 *
 * Read only. One GET, no writes: a token with read access is enough. The repair
 * is printed, never performed.
 *
 * The request deliberately omits per_page and page, because that is the call the
 * 250-commit cap applies to.
 */
const API = 'https://api.github.com';

const CAP = 250;

/**
 * Classify one compare response. Pure. Returns [state, detail].
 *
 * A missing total_commits is its own state rather than a default of zero:
 * defaulting it would report a truncated comparison as complete.
 */
export function verdict(compare) {
  const raw = compare.total_commits;
  if (raw === undefined || raw === null) {
    return ['unknown',
      'no total_commits in the response, so completeness cannot be judged. Do ' +
      'not treat this as complete.'];
  }

  const total = Number(raw);
  const commits = compare.commits ?? [];
  const received = commits.length;
  const files = (compare.files ?? []).length;

  if (total === 0) {
    return ['empty', 'no commits between these refs; head is not ahead of base'];
  }

  if (received >= total) {
    return ['complete',
      `${total} commit(s), all present` +
      (files ? ` (${files} changed file(s))` : '') + '.'];
  }

  if (received === CAP) {
    return ['capped',
      `total_commits is ${total} and ${received} came back: the unpaginated ` +
      `250-commit cap, so ${total - received} commit(s) are missing. The last ` +
      'entry in this list is the head of the comparison, not the 250th commit ' +
      'from the base, so the array is not a contiguous history.'];
  }

  return ['truncated',
    `total_commits is ${total} and ${received} came back, so ${total - received} ` +
    'commit(s) are missing. This is what a paginated read looks like mid-walk; ' +
    'keep paging until the counts agree.'];
}

function arg(name) {
  const i = process.argv.indexOf(`--${name}`);
  return i === -1 ? undefined : process.argv[i + 1];
}

async function get(token, path) {
  const res = await fetch(API + path, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'github-compare-truncation',
    },
  });
  if (res.status === 401) {
    throw new Error('401 from GitHub: GITHUB_TOKEN is missing, malformed or revoked');
  }
  if (res.status === 403) {
    throw new Error('403 from GitHub. If this is a rate limit, GET /rate_limit ' +
                    'reports the reset and does not itself consume quota');
  }
  if (res.status === 404) {
    throw new Error(`404 on ${path}: check the repository and that both refs exist`);
  }
  if (!res.ok) throw new Error(`${res.status} from ${path}`);
  return res.json();
}

async function main() {
  const token = (process.env.GITHUB_TOKEN || "dummy-github-token");
  const repo = arg('repo');
  const base = arg('base');
  const head = arg('head');
  if (!token || !repo || !base || !head) {
    console.error('set GITHUB_TOKEN and pass --repo owner/name --base X --head Y');
    process.exitCode = 2;
    return;
  }

  const path = `/repos/${repo}/compare/${base}...${head}`;
  const [state, detail] = verdict(await get(token, path));

  const line = `${state.padEnd(10)} ${base}...${head}  ${detail}`;
  if (state === 'complete' || state === 'empty') {
    console.log(line);
    return;
  }

  console.warn(line);
  console.warn('  repair: read total_commits first, then page this endpoint with ' +
               'per_page=100 and page=N until you have that many commits, keeping ' +
               'files from the first page only. Or read ' +
               `/repos/${repo}/commits?sha=${head}, which paginates through the ` +
               'Link header and has no 250-commit ceiling.');
  process.exitCode = 1;
}

// Only run when invoked directly, so the test file can import verdict without
// main() running and failing the suite on a missing token.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => { console.error(err.message); process.exitCode = 2; });
}
