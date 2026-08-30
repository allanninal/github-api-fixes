# a pinned X-GitHub-Api-Version stopped being supported

Nothing was deployed. No token was rotated, no permission changed, no dependency was upgraded. On a Tuesday morning every request to the GitHub API starts coming back refused, with a message about an API version, and the only thing that moved is the date. The header responsible was copied out of a documentation sample in 2022 and has not been looked at since.

**Full guide with diagrams:** https://www.allanninal.dev/github/unsupported-api-version/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_api_version_pin.py
node node/github-api-version-pin.mjs
```

## Test it

```bash
pytest python/test_github_api_version_pin.py
node --test node/github-api-version-pin.test.mjs
```
