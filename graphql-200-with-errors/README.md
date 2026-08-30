# GraphQL returns 200 with an errors array and null data

The call succeeded. response.ok is true, the status is 200, the body is valid JSON, and data.repository is null. Downstream something throws Cannot read property 'name' of null, or, far worse, nothing throws at all and the dashboard records that the repository has zero open pull requests. The reason is one line further down the body, in an errors array nobody wrote a branch for.

**Full guide with diagrams:** https://www.allanninal.dev/github/graphql-200-with-errors/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_graphql_envelope.py
node node/github-graphql-envelope.mjs
```

## Test it

```bash
pytest python/test_github_graphql_envelope.py
node --test node/github-graphql-envelope.test.mjs
```
