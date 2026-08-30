# GraphQL data is present but individual fields are null

The query asked for fifty repositories and fifty came back. Nothing threw, nothing logged, the job took the usual eleven seconds. Eight of the fifty have null where diskUsage should be, because the token cannot read that field on a private repository, and the total that gets written to the dashboard is the sum of forty-two numbers presented as the sum of fifty. The response told you which eight, in an errors array that arrived alongside perfectly good data.

**Full guide with diagrams:** https://www.allanninal.dev/github/graphql-partial-data-nulls/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_graphql_partial.py
node node/github-graphql-partial.mjs
```

## Test it

```bash
pytest python/test_github_graphql_partial.py
node --test node/github-graphql-partial.test.mjs
```
