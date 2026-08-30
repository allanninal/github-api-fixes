# the compare endpoint stops at 250 commits and says nothing

The release-notes generator diffs v4.2.0...v4.3.0 and produces a changelog that looks entirely plausible. It has 250 entries. The release contains 812 commits, and the ones it dropped are not the boring ones at the end &mdash; the shape of what came back is not what the code assumed at all.

**Full guide with diagrams:** https://www.allanninal.dev/github/compare-250-commit-cap/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_compare_truncation.py
node node/github-compare-truncation.mjs
```

## Test it

```bash
pytest python/test_github_compare_truncation.py
node --test node/github-compare-truncation.test.mjs
```
