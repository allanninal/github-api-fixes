# GraphQL search stops at the same 1,000 results as REST

The REST search was hitting the thousand-result ceiling, loudly, with a 422 on page eleven that nobody could miss. So the query got rewritten in GraphQL, which is the modern API, has cursors instead of page numbers, and does not document a page limit anywhere obvious. The rewrite works. It runs to completion with no error at all, hasNextPage turns false, the loop exits cleanly, and it has collected 1,000 of the 18,231 issues that issueCount is reporting in the very same response.

**Full guide with diagrams:** https://www.allanninal.dev/github/graphql-search-same-1000-cap/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_graphql_search_ceiling.py
node node/github-graphql-search-ceiling.mjs
```

## Test it

```bash
pytest python/test_github_graphql_search_ceiling.py
node --test node/github-graphql-search-ceiling.test.mjs
```
