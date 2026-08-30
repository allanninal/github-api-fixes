# A GraphQL query times out at 10s and is charged anyway

The nightly job started failing with a 502 about ten seconds into a query that used to take four. The retry logic did the obvious thing and sent it again, three times, with backoff, exactly as it should for a gateway error. By the time somebody looked, the run had produced nothing at all and the hourly point budget was several hundred lighter than a run that produced nothing has any business being.

**Full guide with diagrams:** https://www.allanninal.dev/github/graphql-timeout-point-penalty/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_graphql_timeout.py
node node/github-graphql-timeout.mjs
```

## Test it

```bash
pytest python/test_github_graphql_timeout.py
node --test node/github-graphql-timeout.test.mjs
```
