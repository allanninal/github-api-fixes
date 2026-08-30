# the installation is suspended and every call it makes 403s

Nothing was deployed and nothing was rotated. At some point on Thursday every call the App makes to one organization started coming back 403, and the webhook deliveries that used to arrive every few minutes stopped entirely. The App is still installed &mdash; you can see it in the list, the installation id you stored still resolves &mdash; and it does not work.

**Full guide with diagrams:** https://www.allanninal.dev/github/installation-suspended/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_installation_suspension.py
node node/github-installation-suspension.mjs
```

## Test it

```bash
pytest python/test_github_installation_suspension.py
node --test node/github-installation-suspension.test.mjs
```
