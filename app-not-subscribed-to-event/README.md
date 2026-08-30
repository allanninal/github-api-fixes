# the App was never subscribed to the event it waits for

The handler was written, unit tested against a payload fixture, code reviewed and shipped. In production it has never run. Not once, not slowly, not with an error &mdash; the delivery log has thousands of entries and none of them is that event. There is nothing to debug, because nothing happened.

**Full guide with diagrams:** https://www.allanninal.dev/github/app-not-subscribed-to-event/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_app_event_subscriptions.py
node node/github-app-event-subscriptions.mjs
```

## Test it

```bash
pytest python/test_github_app_event_subscriptions.py
node --test node/github-app-event-subscriptions.test.mjs
```
