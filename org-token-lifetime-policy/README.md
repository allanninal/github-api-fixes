# The org caps token lifetime below your rotation interval

The runbook says rotate the integration token every year, and it has said that since the token was minted with a one-year expiry. This quarter the job started failing against one organization and only that one. The token still authenticates, GET /user answers 200, and everything against the personal account and the other organization keeps working, so it is obviously not an expiry. Except that somebody in that organization set a maximum lifetime for tokens reaching their resources, the token was minted before that and outlives the cap, and the annual rotation was never going to notice a rule that appeared in March.

**Full guide with diagrams:** https://www.allanninal.dev/github/org-token-lifetime-policy/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_token_lifetime.py
node node/github-token-lifetime.mjs
```

## Test it

```bash
pytest python/test_github_token_lifetime.py
node --test node/github-token-lifetime.test.mjs
```
