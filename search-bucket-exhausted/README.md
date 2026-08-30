# search has its own 30-per-minute bucket and drains separately

The job loops over four hundred repositories and runs one search in each. Around the thirtieth it starts returning 403, and the part that makes no sense is that everything else keeps working: the repository reads in the same loop, on the same token, in the same second, are fine. Two buckets, and only one of them is empty. The error message does not mention which.

**Full guide with diagrams:** https://www.allanninal.dev/github/search-bucket-exhausted/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_search_budget.py
node node/github-search-budget.mjs
```

## Test it

```bash
pytest python/test_github_search_budget.py
node --test node/github-search-budget.test.mjs
```
