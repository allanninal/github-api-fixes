# the token is passed as an access_token query parameter

The call returns 401 {"message": "Requires authentication"} for an endpoint that plainly ought to work, and the token in the URL is correct &mdash; you can paste it into a header and watch the same call succeed. That is the small half of this. The large half is that the URL is now in an access log, a CI transcript, a browser history and a support ticket, and nothing about that half produces an error at all.

**Full guide with diagrams:** https://www.allanninal.dev/github/token-in-query-string/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_token_in_url.py
node node/github-token-in-url.mjs
```

## Test it

```bash
pytest python/test_github_token_in_url.py
node --test node/github-token-in-url.test.mjs
```
