# a webhook with no secret sends no signature to verify

The receiver has a signature check. It reads X-Hub-Signature-256, computes an HMAC over the body, compares in constant time, and returns 401 when they differ. It has never returned 401, because the hook it serves has no secret, so GitHub does not send the header, and the check quietly skips itself on every request.

**Full guide with diagrams:** https://www.allanninal.dev/github/webhook-no-secret/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_hook_secret_audit.py
node node/github-hook-secret-audit.mjs
```

## Test it

```bash
pytest python/test_github_hook_secret_audit.py
node --test node/github-hook-secret-audit.test.mjs
```
