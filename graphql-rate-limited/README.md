# GraphQL points run out in a bucket separate from REST

Every GraphQL call is coming back 200 with errors[0].type set to RATE_LIMITED. The health check is green, because the health check is a REST call and REST calls with the same token are working perfectly. Somebody is going to spend an hour looking for a difference between the two code paths, and the difference is not in the code: they are billed from two different buckets, and only one of them is empty.

**Full guide with diagrams:** https://www.allanninal.dev/github/graphql-rate-limited/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_graphql_points.py
node node/github-graphql-points.mjs
```

## Test it

```bash
pytest python/test_github_graphql_points.py
node --test node/github-graphql-points.test.mjs
```
