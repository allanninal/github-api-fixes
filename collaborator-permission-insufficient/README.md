# The scope is right, the account's role on the repo is read

The bot has a token with the repo scope. It reads the repository, lists the pull requests, fetches the diff, and then the merge comes back 403. Somebody checks the token, sees repo ticked, mints a wider one with workflow and admin:org on it for good measure, and gets the same 403 back in the same millisecond. The scopes were never the ceiling. The account those scopes act on behalf of is a collaborator with read on that repository, and no token minted by that account will ever be able to merge anything into it.

**Full guide with diagrams:** https://www.allanninal.dev/github/collaborator-permission-insufficient/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_repo_role.py
node node/github-repo-role.mjs
```

## Test it

```bash
pytest python/test_github_repo_role.py
node --test node/github-repo-role.test.mjs
```
