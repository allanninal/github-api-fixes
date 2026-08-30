# Nobody measured what the GraphQL query actually costs

The dashboard query has been running every fifteen seconds for a year. Last Tuesday somebody added one nested field to it, the review was two lines long and entirely reasonable, and on Thursday afternoon the whole integration started returning RATE_LIMITED at about ten past two. Nothing in the pull request said the price had gone from three points to fourteen, because nobody had ever written down that it was three.

**Full guide with diagrams:** https://www.allanninal.dev/github/graphql-cost-not-measured/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_graphql_cost.py
node node/github-graphql-cost.mjs
```

## Test it

```bash
pytest python/test_github_graphql_cost.py
node --test node/github-graphql-cost.test.mjs
```
