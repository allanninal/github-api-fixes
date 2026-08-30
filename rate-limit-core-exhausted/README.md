# core REST quota is exhausted and every call returns 403

Every endpoint fails at once, with the same status, in the same second. That pattern says outage or bad credentials, and it is neither: the hourly bucket is empty, and an empty bucket refuses everything equally. The awkward part is that by the time you are reading the 403 the interesting question has already gone past. Not are we out, but at what rate did we spend it, and would that rate have fitted.

**Full guide with diagrams:** https://www.allanninal.dev/github/rate-limit-core-exhausted/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_quota_forecast.py
node node/github-quota-forecast.mjs
```

## Test it

```bash
pytest python/test_github_quota_forecast.py
node --test node/github-quota-forecast.test.mjs
```
