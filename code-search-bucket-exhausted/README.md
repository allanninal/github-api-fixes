# code search is billed to its own 10 a minute bucket

The script walks the org, calling GET /search/code once per repository. It gets through nine of them. The tenth returns 403, and GET /rate_limit says you have 4,987 requests left in the hour. Both statements are correct, because the request that was refused was never being counted in the bucket you just looked at.

**Full guide with diagrams:** https://www.allanninal.dev/github/code-search-bucket-exhausted/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_code_search_budget.py
node node/github-code-search-budget.mjs
```

## Test it

```bash
pytest python/test_github_code_search_budget.py
node --test node/github-code-search-budget.test.mjs
```
