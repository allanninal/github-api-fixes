# the client ignores retry-after and keeps hammering the API

The log shows four hundred consecutive 403s, one second apart, for eleven minutes. The retry logic is working perfectly: it catches the error, waits, tries again, never gives up. GitHub said retry-after: 120 on the very first one, and the client threw that header away along with the rest of the response.

**Full guide with diagrams:** https://www.allanninal.dev/github/retry-after-ignored/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_backoff_plan.py
node node/github-backoff-plan.mjs
```

## Test it

```bash
pytest python/test_github_backoff_plan.py
node --test node/github-backoff-plan.test.mjs
```
