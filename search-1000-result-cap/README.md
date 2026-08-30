# search returns at most 1,000 results whatever total_count says

The response says "total_count": 24831. You page through it at 100 per request, and on page 11 the API returns 422 Validation Failed with "Only the first 1000 search results are available". The count was true. The results were never there to fetch &mdash; total_count describes the match set, not the part of it you are allowed to page through.

**Full guide with diagrams:** https://www.allanninal.dev/github/search-1000-result-cap/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_search_cap_audit.py
node node/github-search-cap-audit.mjs
```

## Test it

```bash
pytest python/test_github_search_cap_audit.py
node --test node/github-search-cap-audit.test.mjs
```
