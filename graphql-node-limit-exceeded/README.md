# A nested GraphQL query requests more than 500,000 nodes

The query worked all week against the test org, which has four repositories. Pointed at the real one it comes back rejected with MAX_NODE_LIMIT_EXCEEDED before a single row is read. The instinct is that the org is too big, so somebody reduces the date range and it fails identically, because the limit was never about how much data exists. It is about the numbers written in the query, and those did not change.

**Full guide with diagrams:** https://www.allanninal.dev/github/graphql-node-limit-exceeded/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_graphql_nodes.py
node node/github-graphql-nodes.mjs
```

## Test it

```bash
pytest python/test_github_graphql_nodes.py
node --test node/github-graphql-nodes.test.mjs
```
