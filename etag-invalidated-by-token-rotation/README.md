# rotating the token invalidates every cached ETag at once

The graph is a sawtooth and it is a very tidy one. Quota consumption sits near zero for fifty-odd minutes, jumps, and settles back, every hour, on the hour. Nothing in the schedule matches. The poller runs every thirty seconds and has done for months. What runs hourly is the installation token.

**Full guide with diagrams:** https://www.allanninal.dev/github/etag-invalidated-by-token-rotation/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_etag_credential_check.py
node node/github-etag-credential-check.mjs
```

## Test it

```bash
pytest python/test_github_etag_credential_check.py
node --test node/github-etag-credential-check.test.mjs
```
