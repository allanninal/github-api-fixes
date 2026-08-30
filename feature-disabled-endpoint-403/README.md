# A 403 that means the feature is disabled, not the permission

The security dashboard job gets 403 from /code-scanning/alerts. That is the status a missing grant produces, so somebody adds the security-events permission. Same 403. They swap the fine-grained token for an App installation with every security permission ticked. Same 403. Two weeks later a repository admin opens the settings page and code scanning has never been turned on for that repository, which is a checkbox, not a grant, and no credential in the world was going to open it.

**Full guide with diagrams:** https://www.allanninal.dev/github/feature-disabled-endpoint-403/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_feature_flags.py
node node/github-feature-flags.mjs
```

## Test it

```bash
pytest python/test_github_feature_flags.py
node --test node/github-feature-flags.test.mjs
```
