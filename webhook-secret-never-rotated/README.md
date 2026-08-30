# the webhook secret is set and has never been rotated

There is no symptom. The hook works, the signatures verify, the deliveries are green, and the secret that authenticates every event has been sitting in the same configuration file since the integration was built. It has outlasted two contractors, a laptop that was never wiped, and a support ticket where somebody pasted the receiver's environment into a chat window to get help.

**Full guide with diagrams:** https://www.allanninal.dev/github/webhook-secret-never-rotated/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_hook_secret_age.py
node node/github-hook-secret-age.mjs
```

## Test it

```bash
pytest python/test_github_hook_secret_age.py
node --test node/github-hook-secret-age.test.mjs
```
