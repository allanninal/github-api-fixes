# webhook deliveries are failing and nobody reads the log

Someone opens a pull request and the bot that should comment on it says nothing. You grep your receiver's logs for the last hour and find no request at all, which points the finger at GitHub. It is not GitHub. The delivery happened, your server answered 502, and GitHub wrote that down in a log you have never opened.

**Full guide with diagrams:** https://www.allanninal.dev/github/webhook-deliveries-failing/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_hook_delivery_audit.py
node node/github-hook-delivery-audit.mjs
```

## Test it

```bash
pytest python/test_github_hook_delivery_audit.py
node --test node/github-hook-delivery-audit.test.mjs
```
