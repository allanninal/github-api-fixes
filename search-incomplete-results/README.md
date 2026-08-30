# incomplete_results is true and the search answer is partial

The same search runs every morning and the number it reports moves: 412 on Monday, 380 on Tuesday, 412 again on Wednesday. Nothing failed. Every response was a 200 with well-formed JSON and a sensible-looking list of items. The field that says the answer was cut short is sitting at the top of every one of those payloads, and no code in the pipeline has ever read it.

**Full guide with diagrams:** https://www.allanninal.dev/github/search-incomplete-results/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_search_incomplete.py
node node/github-search-incomplete.mjs
```

## Test it

```bash
pytest python/test_github_search_incomplete.py
node --test node/github-search-incomplete.test.mjs
```
