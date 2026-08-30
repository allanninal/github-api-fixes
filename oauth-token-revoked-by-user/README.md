# one user revoked your app and only their token is dead

One customer's sync has been failing since Thursday. Everybody else is fine. The token has not expired, because OAuth user tokens do not expire; nothing was deployed; the error is 401 Bad credentials and the retry loop has been dutifully re-attempting it every fifteen minutes for four days. The user clicked Revoke on a settings page you will never see.

**Full guide with diagrams:** https://www.allanninal.dev/github/oauth-token-revoked-by-user/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_user_token_liveness.py
node node/github-user-token-liveness.mjs
```

## Test it

```bash
pytest python/test_github_user_token_liveness.py
node --test node/github-user-token-liveness.test.mjs
```
