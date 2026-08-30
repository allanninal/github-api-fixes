# SSL verification is switched off on the webhook

The URL starts with https. The delivery log is clean, every attempt is a 200, and the hook has a secret so the payloads are signed. Everything a review asks about is green. One field in the same config object says &quot;insecure_ssl&quot;: &quot;1&quot;, which means GitHub has not looked at your certificate since somebody set that during the initial deploy in 2023.

**Full guide with diagrams:** https://www.allanninal.dev/github/webhook-insecure-ssl/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_hook_ssl_verification.py
node node/github-hook-ssl-verification.mjs
```

## Test it

```bash
pytest python/test_github_hook_ssl_verification.py
node --test node/github-hook-ssl-verification.test.mjs
```
