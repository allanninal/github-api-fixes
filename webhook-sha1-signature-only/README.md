# the receiver still checks the legacy SHA-1 signature

The receiver verifies. It reads a signature header, computes an HMAC over the raw body, compares in constant time and returns 401 when they differ. It has been doing that since 2017, which is the problem: the header it reads is X-Hub-Signature, the SHA-1 one GitHub keeps sending for the sake of receivers exactly like this one.

**Full guide with diagrams:** https://www.allanninal.dev/github/webhook-sha1-signature-only/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_hook_signature_headers.py
node node/github-hook-signature-headers.mjs
```

## Test it

```bash
pytest python/test_github_hook_signature_headers.py
node --test node/github-hook-signature-headers.test.mjs
```
