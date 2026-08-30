# Every call succeeds and reports on a fork, not the upstream

The quarterly report says the platform repository had four merged pull requests, no releases and eleven open issues. It is a repository with nine thousand issues. Nothing failed to produce that report: every call returned 200, the pagination was followed properly, the retries never fired, the JSON parsed. The configuration was copied a year ago from an engineer's personal fork, and a fork is a different repository with its own issues, its own releases and its own branches. The integration has been right about the wrong object for four quarters.

**Full guide with diagrams:** https://www.allanninal.dev/github/fork-vs-upstream-confusion/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_fork_or_upstream.py
node node/github-fork-or-upstream.mjs
```

## Test it

```bash
pytest python/test_github_fork_or_upstream.py
node --test node/github-fork-or-upstream.test.mjs
```
