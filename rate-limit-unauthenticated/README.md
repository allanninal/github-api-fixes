# requests go out anonymous and are capped at 60 an hour

It works on the first run and dies on the fourth. On a laptop it survives a couple of minutes; in CI, behind a shared NAT address, it is refused almost immediately and the run before yours gets the blame. Nothing in the code is wrong. The token simply is not arriving, and GitHub does not treat that as an error. It serves you anyway, as a stranger, from a bucket sixty deep.

**Full guide with diagrams:** https://www.allanninal.dev/github/rate-limit-unauthenticated/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_auth_tier_check.py
node node/github-auth-tier-check.mjs
```

## Test it

```bash
pytest python/test_github_auth_tier_check.py
node --test node/github-auth-tier-check.test.mjs
```
