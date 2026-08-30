# some endpoints refuse an installation token whatever it holds

The App is installed. The JWT signs, the installation token mints, and nineteen calls in a row return exactly what they should. Then GET /user comes back 403 {"message": "Resource not accessible by integration"}, somebody adds a permission, every installer is emailed to accept the upgrade, and the same 403 arrives again the next morning.

**Full guide with diagrams:** https://www.allanninal.dev/github/installation-token-rejected-by-endpoint/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_endpoint_audience.py
node node/github-endpoint-audience.mjs
```

## Test it

```bash
pytest python/test_github_endpoint_audience.py
node --test node/github-endpoint-audience.test.mjs
```
