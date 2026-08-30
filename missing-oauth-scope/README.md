# the endpoint accepts a scope your token was never given

Nineteen calls work. The twentieth returns 403 {"message": "Must have admin rights to Repository."}, or worse, a bare 404 on a repository that is open in a browser tab beside you. The token is valid, it is not expired, it has not been revoked, and it is missing exactly one word from the list it was created with.

**Full guide with diagrams:** https://www.allanninal.dev/github/missing-oauth-scope/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_scope_diff.py
node node/github-scope-diff.mjs
```

## Test it

```bash
pytest python/test_github_scope_diff.py
node --test node/github-scope-diff.test.mjs
```
