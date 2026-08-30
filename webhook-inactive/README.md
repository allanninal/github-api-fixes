# the webhook exists but somebody switched it off

The hook is right there in the repository's settings, pointed at the right URL, subscribed to the right events, with a secret set and a green tick beside it from the last time anyone looked. The delivery log is not full of failures. It is empty, and it has been empty for five weeks. Nothing is broken because nothing is running: active is false.

**Full guide with diagrams:** https://www.allanninal.dev/github/webhook-inactive/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_hook_active_audit.py
node node/github-hook-active-audit.mjs
```

## Test it

```bash
pytest python/test_github_hook_active_audit.py
node --test node/github-hook-active-audit.test.mjs
```
