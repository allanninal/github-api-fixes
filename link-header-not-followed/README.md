# only the first page is read because the Link header is ignored

The request returned 200. The JSON is a well-formed array of pull requests. Your audit read it, counted 30, and reported that the repository is tidy. There are 340 open pull requests. Nothing failed, nothing was logged, and the number you are now acting on is wrong by an order of magnitude &mdash; the answer was complete for one page and the rest was advertised in a header nobody read.

**Full guide with diagrams:** https://www.allanninal.dev/github/link-header-not-followed/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_link_header_audit.py
node node/github-link-header-audit.mjs
```

## Test it

```bash
pytest python/test_github_link_header_audit.py
node --test node/github-link-header-audit.test.mjs
```
