# A GraphQL connection asks for first: 500 and is rejected

The query is fifteen lines long and it has never run once. The body comes back with Argument 'first' on Field 'issues' has an invalid value (500), there is no data key in it at all, and the 500 is not written anywhere in the document — it is the default on a variable somebody added a year ago. The code was ported from a REST client where per_page=500 quietly became 100 and the job kept working, so nobody ever learned that the number was wrong.

**Full guide with diagrams:** https://www.allanninal.dev/github/graphql-first-over-100/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_graphql_slice.py
node node/github-graphql-slice.mjs
```

## Test it

```bash
pytest python/test_github_graphql_slice.py
node --test node/github-graphql-slice.test.mjs
```
