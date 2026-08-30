# the GitHub App has no webhook URL configured

The App is installed on forty repositories, the permissions were approved, the event subscriptions are exactly right, and it has never once reacted to anything. There is nothing in the delivery log to read, no failures to group, no status codes to explain. Nothing is failing, because nothing is being attempted.

**Full guide with diagrams:** https://www.allanninal.dev/github/app-webhook-url-unset/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_app_hook_config.py
node node/github-app-hook-config.mjs
```

## Test it

```bash
pytest python/test_github_app_hook_config.py
node --test node/github-app-hook-config.test.mjs
```
