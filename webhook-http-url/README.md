# the webhook posts your payloads to an http:// URL

The hook works. It has a secret, it is subscribed to the right events, the delivery log is a column of 200s, and insecure_ssl reads 0, which is the field the last security review looked at. The URL is http://hooks.internal.acme.io/github, so every payload and the signature that authenticates it have been crossing the network as plain text since the day it was created.

**Full guide with diagrams:** https://www.allanninal.dev/github/webhook-http-url/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_hook_transport.py
node node/github-hook-transport.mjs
```

## Test it

```bash
pytest python/test_github_hook_transport.py
node --test node/github-hook-transport.test.mjs
```
