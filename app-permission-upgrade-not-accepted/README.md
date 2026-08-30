# a new App permission that installers never accepted

The permission was added a fortnight ago. The App's settings page shows it, the pull request that used it was merged, and the feature works &mdash; for about two thirds of customers. The rest still get 403 {"message": "Resource not accessible by integration"} on exactly the call the permission was added for, and there is no pattern in which ones. Big orgs and small, old installs and new.

**Full guide with diagrams:** https://www.allanninal.dev/github/app-permission-upgrade-not-accepted/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_permission_upgrade_lag.py
node node/github-permission-upgrade-lag.mjs
```

## Test it

```bash
pytest python/test_github_permission_upgrade_lag.py
node --test node/github-permission-upgrade-lag.test.mjs
```
