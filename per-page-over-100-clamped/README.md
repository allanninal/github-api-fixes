# per_page above 100 is clamped and never rejected

Somebody set per_page=500 to cut the number of round trips, and the request came back with exactly one hundred items and a 200. One hundred is fewer than five hundred, so the loop concluded it had reached the end and stopped. There was no 422, no warning header and nothing in the logs. Four fifths of the collection was never read, and the report built on it looks entirely finished.

**Full guide with diagrams:** https://www.allanninal.dev/github/per-page-over-100-clamped/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_per_page_clamp.py
node node/github-per-page-clamp.mjs
```

## Test it

```bash
pytest python/test_github_per_page_clamp.py
node --test node/github-per-page-clamp.test.mjs
```
