# a classic PAT passed its expiry and everything broke at once

The integration ran for eleven months without anyone touching it. At 09:14 on a Tuesday every call started returning 401 Bad credentials, all at once, on every endpoint, with no deploy, no repository change and no org announcement. A classic personal access token reached its expiry date. So did a great many other theories that morning, and the API will not tell you which one was right.

**Full guide with diagrams:** https://www.allanninal.dev/github/classic-pat-expired/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_credential_differential.py
node node/github-credential-differential.mjs
```

## Test it

```bash
pytest python/test_github_credential_differential.py
node --test node/github-credential-differential.test.mjs
```
