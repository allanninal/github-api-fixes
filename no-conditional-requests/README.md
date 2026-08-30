# polling without ETags spends full quota on unchanged data

The dashboard polls eight endpoints every thirty seconds and burns through 5,000 requests before lunch. Almost nothing it fetches has changed. GitHub has been sending an etag on every one of those responses, and every response that comes back 304 Not Modified is free &mdash; it does not count against the rate limit at all. This is the one problem in this section where the fix pays for itself in a number you can print.

**Full guide with diagrams:** https://www.allanninal.dev/github/no-conditional-requests/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_etag_saving.py
node node/github-etag-saving.mjs
```

## Test it

```bash
pytest python/test_github_etag_saving.py
node --test node/github-etag-saving.test.mjs
```
