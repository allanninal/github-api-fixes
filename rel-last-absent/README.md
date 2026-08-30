# the Link header has no rel=last so the page count breaks

The pager was written properly. It reads the Link header, it pulls the page number out of rel="last", and it uses that number to size the job before it starts: a progress bar, a worker pool over the page range, an estimate in the log line. On one endpoint the header comes back with a rel="next" and no rel="last" at all, and the job reports that it read one page of a one-page collection. It read one page of nine hundred.

**Full guide with diagrams:** https://www.allanninal.dev/github/rel-last-absent/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_rel_last_absent.py
node node/github-rel-last-absent.mjs
```

## Test it

```bash
pytest python/test_github_rel_last_absent.py
node --test node/github-rel-last-absent.test.mjs
```
