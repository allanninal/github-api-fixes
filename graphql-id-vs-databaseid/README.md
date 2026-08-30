# GraphQL node ids get stored where REST ids are expected

The importer that reads through REST and the sync job that reads through GraphQL have been running side by side for a year. Neither has ever thrown. The join between their two tables returns nothing, so somebody widens it to a left join, sees a wall of nulls, and starts checking timestamps. There is nothing wrong with the timestamps. One table has 1347 in its id column and the other has MDU6SXNzdWUxMzQ3, and those are the same issue.

**Full guide with diagrams:** https://www.allanninal.dev/github/graphql-id-vs-databaseid/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_graphql_id_crosswalk.py
node node/github-graphql-id-crosswalk.mjs
```

## Test it

```bash
pytest python/test_github_graphql_id_crosswalk.py
node --test node/github-graphql-id-crosswalk.test.mjs
```
