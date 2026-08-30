# the App's rate limit never grew with the installation

The App serves an organization with four hundred repositories, and it throttles like a personal access token. Somebody quotes the number they remember &mdash; Apps get more, Apps scale with the installation, we will be fine at this volume &mdash; and the plan was built on it. GET /rate_limit says 5000, the same as the laptop script it replaced.

**Full guide with diagrams:** https://www.allanninal.dev/github/app-rate-limit-not-scaling/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_app_limit_ceiling.py
node node/github-app-limit-ceiling.mjs
```

## Test it

```bash
pytest python/test_github_app_limit_ceiling.py
node --test node/github-app-limit-ceiling.test.mjs
```
