# A mutation costs five secondary points, a query costs one

The read job runs all day without complaint. The write job, which sends fewer requests, dies eleven minutes in with 403 and You have exceeded a secondary rate limit. Somebody checks GET /rate_limit, sees four thousand GraphQL points still sitting there unspent, and concludes GitHub is wrong. GitHub is not wrong. The bucket that emptied is not the one being looked at, and the reason the write job reached it first is that every document containing a mutation is priced at five times a read.

**Full guide with diagrams:** https://www.allanninal.dev/github/graphql-mutation-secondary-cost/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_graphql_mutation_budget.py
node node/github-graphql-mutation-budget.mjs
```

## Test it

```bash
pytest python/test_github_graphql_mutation_budget.py
node --test node/github-graphql-mutation-budget.test.mjs
```
