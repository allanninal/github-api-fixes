# rows move between pages and the walk skips records

The sync reads the Link header, follows rel="next" to the very end and asks for a hundred items a page. It is, by every check in this section, a correct pagination loop. It also misses about one issue a night and occasionally imports the same one twice, and nobody can reproduce it, because reproducing it requires somebody to touch an issue during the eleven seconds the walk is passing over it.

**Full guide with diagrams:** https://www.allanninal.dev/github/unstable-sort-duplicates/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_unstable_sort.py
node node/github-unstable-sort.mjs
```

## Test it

```bash
pytest python/test_github_unstable_sort.py
node --test node/github-unstable-sort.test.mjs
```
