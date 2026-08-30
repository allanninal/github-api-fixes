# over 100 concurrent requests trips a secondary rate limit

Someone replaces a for loop with a Promise.all and the job goes from nine minutes to forty seconds, once. The next run returns 403 on two thirds of the requests. You check the quota, because a 403 from GitHub means the quota, and the quota says four thousand eight hundred requests left. Both numbers are true. They are about different limits.

**Full guide with diagrams:** https://www.allanninal.dev/github/secondary-limit-concurrency/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_concurrency_probe.py
node node/github-concurrency-probe.mjs
```

## Test it

```bash
pytest python/test_github_concurrency_probe.py
node --test node/github-concurrency-probe.test.mjs
```
