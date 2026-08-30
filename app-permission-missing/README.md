# resource not accessible by integration on one endpoint

Nineteen endpoints work. The twentieth returns 403 {"message":"Resource not accessible by integration"}, which names no permission, no resource and no level, and reads like a platform bug rather than a configuration one. It is the one failure in this cluster where GitHub actually tells you the answer: it is in the x-accepted-github-permissions response header on that very 403, which almost no HTTP client shows you by default.

**Full guide with diagrams:** https://www.allanninal.dev/github/app-permission-missing/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_app_permission_diff.py
node node/github-app-permission-diff.mjs
```

## Test it

```bash
pytest python/test_github_app_permission_diff.py
node --test node/github-app-permission-diff.test.mjs
```
