# the integration polls for events a webhook would push

The integration works. It notices new pull requests, it picks up comments, it has never lost anything. It also makes 4,320 requests an hour to do it, notices each of those events an average of thirty seconds after it happened, and would notice nothing at all if the poll ran once a day instead. There is no bug here. There is a design that was never revisited.

**Full guide with diagrams:** https://www.allanninal.dev/github/polling-instead-of-webhooks/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_webhook_vs_poll.py
node node/github-webhook-vs-poll.mjs
```

## Test it

```bash
pytest python/test_github_webhook_vs_poll.py
node --test node/github-webhook-vs-poll.test.mjs
```
