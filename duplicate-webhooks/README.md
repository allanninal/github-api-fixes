# the same webhook URL is registered on the org and the repo

The bot comments twice on every pull request. Someone checks the receiver for a retry loop, finds none, and blames GitHub for sending duplicates. GitHub is sending exactly one copy of the event to each hook that asked for it &mdash; and two hooks asked, one on the repository and one on the organization, both pointing at the same URL.

**Full guide with diagrams:** https://www.allanninal.dev/github/duplicate-webhooks/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_duplicate_hooks.py
node node/github-duplicate-hooks.mjs
```

## Test it

```bash
pytest python/test_github_duplicate_hooks.py
node --test node/github-duplicate-hooks.test.mjs
```
