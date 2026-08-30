# the hook is not subscribed to the event you are waiting for

The handler was written, reviewed, unit tested against a saved payload, and deployed. In production it has never executed once. There is no error anywhere because there is no failure anywhere: the hook was created years ago with push and pull_request, and the event your handler waits for has never been sent to it.

**Full guide with diagrams:** https://www.allanninal.dev/github/webhook-event-not-subscribed/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_hook_event_coverage.py
node node/github-hook-event-coverage.mjs
```

## Test it

```bash
pytest python/test_github_hook_event_coverage.py
node --test node/github-hook-event-coverage.test.mjs
```
