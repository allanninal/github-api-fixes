# the repository is archived so every write returns 403

The labelling bot has been failing on one repository since March. Everything it reads comes back perfectly: issues, labels, the repository object itself, all 200. The moment it tries to add a label it gets 403, so the on-call runbook says permissions, so somebody widens the token, and it still gets 403. The token was never the problem. Somebody archived the repository seven months ago, which makes it read-only for everyone and every credential, and the bot has been retrying a request that will not be accepted by anyone until it is unarchived.

**Full guide with diagrams:** https://www.allanninal.dev/github/repo-archived-writes-403/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_archived_repo_guard.py
node node/github-archived-repo-guard.mjs
```

## Test it

```bash
pytest python/test_github_archived_repo_guard.py
node --test node/github-archived-repo-guard.test.mjs
```
