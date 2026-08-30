# the installation token was narrowed below what the job needs

The App is installed on the repository. You can see the installation, the repository is in its list, the permissions are generous, and one job gets 404 every time it touches that repository. The other four jobs, using the same App, on the same repository, are fine. Nobody has changed the App in months.

**Full guide with diagrams:** https://www.allanninal.dev/github/app-token-scoped-down-too-far/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_token_reach.py
node node/github-token-reach.mjs
```

## Test it

```bash
pytest python/test_github_token_reach.py
node --test node/github-token-reach.test.mjs
```
