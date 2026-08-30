# a 404 that means the App is not installed on that repo

The repository is public. You can open it in a browser without logging in. The App reads eleven other repositories in the same organization with the same token, and this one comes back 404 Not Found. So the search starts with permissions, and permissions are not the problem, because the App was never installed here at all.

**Full guide with diagrams:** https://www.allanninal.dev/github/app-not-installed-on-repo/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_app_installation_presence.py
node node/github-app-installation-presence.mjs
```

## Test it

```bash
pytest python/test_github_app_installation_presence.py
node --test node/github-app-installation-presence.test.mjs
```
