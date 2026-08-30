# expensive requests are killed at ten seconds with a 502

One call in the whole integration returns 502 Bad Gateway. Everything else is fine, the token is fine, the status page is green, and the call works on every repository except the big one. So the retry wrapper does what retry wrappers do: it waits a second and issues the identical expensive request, which takes the identical ten seconds and fails in the identical way, three times, before the job gives up and pages somebody.

**Full guide with diagrams:** https://www.allanninal.dev/github/request-timeout-502/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_timeout_502.py
node node/github-timeout-502.mjs
```

## Test it

```bash
pytest python/test_github_timeout_502.py
node --test node/github-timeout-502.test.mjs
```
