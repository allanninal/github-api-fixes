# the hook subscribes to every event with a wildcard

The hook was set up in a hurry, with * in the events list, because nobody was sure yet which events the integration would need. That was two years ago. The receiver now handles four event types and is delivered somewhere north of forty, and every one of the ones it does not want still arrives, still gets its signature verified, and is still thrown away. Nothing has ever failed.

**Full guide with diagrams:** https://www.allanninal.dev/github/webhook-wildcard-events/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_hook_event_volume.py
node node/github-hook-event-volume.mjs
```

## Test it

```bash
pytest python/test_github_hook_event_volume.py
node --test node/github-hook-event-volume.test.mjs
```
