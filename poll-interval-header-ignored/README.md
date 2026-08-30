# the x-poll-interval header is ignored on events endpoints

The events consumer polls every five seconds because five seconds felt responsive. It gets the same page back seven hundred times an hour. On every one of those responses GitHub has been returning a header that says how long to wait before the next one, and the client has never read it.

**Full guide with diagrams:** https://www.allanninal.dev/github/poll-interval-header-ignored/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_poll_interval_check.py
node node/github-poll-interval-check.mjs
```

## Test it

```bash
pytest python/test_github_poll_interval_check.py
node --test node/github-poll-interval-check.test.mjs
```
