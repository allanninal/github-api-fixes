# a firewall allow-list no longer matches GitHub's hook IPs

Some deliveries land and some do not, with no pattern anybody can find. It is not the event type, it is not the payload size, it is not the time of day. The hook is configured correctly, the receiver is up, and the delivery log shows connection failures scattered among successes. Nothing is wrong with the webhook. The problem is a text file on a firewall, written correctly two years ago.

**Full guide with diagrams:** https://www.allanninal.dev/github/webhook-ip-allowlist-drift/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_meta_hook_ranges.py
node node/github-meta-hook-ranges.mjs
```

## Test it

```bash
pytest python/test_github_meta_hook_ranges.py
node --test node/github-meta-hook-ranges.test.mjs
```
