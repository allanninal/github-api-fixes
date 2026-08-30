# a hot endpoint burns 900 points a minute and gets throttled

One endpoint in the job keeps failing and the rest are fine. It fails for about a minute, recovers, and fails again twenty minutes later. The hourly quota is barely touched and the concurrency is one, because someone already serialised it after the last incident. What is left is the cap nobody budgets for: not how many requests you make, but how much work you asked one path to do inside sixty seconds.

**Full guide with diagrams:** https://www.allanninal.dev/github/secondary-limit-points-per-minute/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_endpoint_cost_audit.py
node node/github-endpoint-cost-audit.mjs
```

## Test it

```bash
pytest python/test_github_endpoint_cost_audit.py
node --test node/github-endpoint-cost-audit.test.mjs
```
