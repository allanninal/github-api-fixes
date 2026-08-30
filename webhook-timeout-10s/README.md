# the receiver takes longer than 10 seconds and times out

The delivery log has started showing timed out against a handler that, according to your own logs, finished the job perfectly. It ran, it did the work, it wrote the record, it returned 200 &mdash; twelve seconds after it started. GitHub stopped listening at ten and filed the delivery as a failure, and the redelivery it may send will do the same twelve seconds of work again.

**Full guide with diagrams:** https://www.allanninal.dev/github/webhook-timeout-10s/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_hook_delivery_duration.py
node node/github-hook-delivery-duration.mjs
```

## Test it

```bash
pytest python/test_github_hook_delivery_duration.py
node --test node/github-hook-delivery-duration.test.mjs
```
