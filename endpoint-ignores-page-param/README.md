# the endpoint ignores page and returns page one forever

The collector increments a page counter, exactly as it does against a dozen other endpoints, and every request comes back 200 with a full page of rows. It never finishes. Either it runs until something kills it, or it stops at an arbitrary page cap and hands over a dataset that is the same thirty records repeated forty times. The endpoint has been returning page one to every request since the first one.

**Full guide with diagrams:** https://www.allanninal.dev/github/endpoint-ignores-page-param/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_page_param_ignored.py
node node/github-page-param-ignored.mjs
```

## Test it

```bash
pytest python/test_github_page_param_ignored.py
node --test node/github-page-param-ignored.test.mjs
```
