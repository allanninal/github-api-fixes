# the installation token expired an hour into the job

The migration ran for fifty-eight minutes and processed eleven thousand repositories. Then every request started returning 401 Bad credentials &mdash; not some of them, all of them, from one second to the next. Restarting the job fixes it, which is the detail that sends everybody looking for a memory leak. It is not a leak. The process outlived its own credential.

**Full guide with diagrams:** https://www.allanninal.dev/github/installation-token-expired/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_installation_token_age.py
node node/github-installation-token-age.mjs
```

## Test it

```bash
pytest python/test_github_installation_token_age.py
node --test node/github-installation-token-age.test.mjs
```
