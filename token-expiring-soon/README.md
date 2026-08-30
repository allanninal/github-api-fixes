# the token expires in days and nothing is watching the clock

Nothing is broken. That is the entire point of this note: it is the one that runs before the outage rather than after it. Somewhere in your environment is a credential with six days left on it, and the only thing standing between you and a 09:14 Tuesday is that nobody has read a header GitHub has been sending on every single response for months.

**Full guide with diagrams:** https://www.allanninal.dev/github/token-expiring-soon/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_token_expiry_watch.py
node node/github-token-expiry-watch.mjs
```

## Test it

```bash
pytest python/test_github_token_expiry_watch.py
node --test node/github-token-expiry-watch.test.mjs
```
