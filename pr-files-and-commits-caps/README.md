# a pull request's files and commits lists are both capped

The review bot posts its summary on a nine-hundred-file pull request and says three files changed. Nobody reads past the first line, because a bot that has been right for a year is not a thing people audit. The pull request object knew the real number the whole time. It is just that changed_files lives on the pull request and the file list lives one URL further down, and nothing in either response mentions the other.

**Full guide with diagrams:** https://www.allanninal.dev/github/pr-files-and-commits-caps/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_pr_truncation.py
node node/github-pr-truncation.mjs
```

## Test it

```bash
pytest python/test_github_pr_truncation.py
node --test node/github-pr-truncation.test.mjs
```
