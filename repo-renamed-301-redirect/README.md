# the repository was renamed and every call now 301s

Somebody renamed the repository on a Tuesday. Nothing broke, which is the problem: GitHub left a redirect at the old path and most clients follow it without mentioning it, so the integration kept working against a name that no longer exists. The config still says acme/platform-api. The repository is called acme/core-api. Every request the job makes takes two round trips instead of one, and has done for eight months.

**Full guide with diagrams:** https://www.allanninal.dev/github/repo-renamed-301-redirect/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_repo_renamed.py
node node/github-repo-renamed.mjs
```

## Test it

```bash
pytest python/test_github_repo_renamed.py
node --test node/github-repo-renamed.test.mjs
```
