# Nested GraphQL connections truncate at 100 per parent

The pagination loop is correct. It reads pageInfo.hasNextPage, follows endCursor, and walks every repository in the organisation without missing one. Inside each of those repositories the query also asks for pull requests, and every repository returns exactly one hundred of them, including the one that has four hundred and six. The response says so, in a totalCount sitting three lines above the truncated list, and nothing anywhere raises an error.

**Full guide with diagrams:** https://www.allanninal.dev/github/graphql-nested-pagination-ignored/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_graphql_nested.py
node node/github-graphql-nested.mjs
```

## Test it

```bash
pytest python/test_github_graphql_nested.py
node --test node/github-graphql-nested.test.mjs
```
