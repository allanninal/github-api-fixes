# the hook sends form-encoded bodies to a JSON receiver

The handler is written, deployed and subscribed to the right event. GitHub says the delivery succeeded. Your receiver says it got a request and found nothing in it &mdash; or says nothing at all, because it returned 200 having parsed an empty object out of a body it never understood. Every field in the hook looks right, because the field that is wrong is the one nobody reads.

**Full guide with diagrams:** https://www.allanninal.dev/github/webhook-content-type-mismatch/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_hook_content_type.py
node node/github-hook-content-type.mjs
```

## Test it

```bash
pytest python/test_github_hook_content_type.py
node --test node/github-hook-content-type.test.mjs
```
