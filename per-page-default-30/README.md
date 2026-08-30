# per_page is unset so every list costs 3.3x more requests

The job is correct. It follows rel="next" to the end, it reads every issue, and it burns through the hourly quota by lunchtime. Nothing is broken and nothing needs debugging &mdash; it is simply making three and a third times as many requests as it needs to, because per_page was never set and the default is 30.

**Full guide with diagrams:** https://www.allanninal.dev/github/per-page-default-30/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_per_page_audit.py
node node/github-per-page-audit.mjs
```

## Test it

```bash
pytest python/test_github_per_page_audit.py
node --test node/github-per-page-audit.test.mjs
```
