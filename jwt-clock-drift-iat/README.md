# clock drift puts the JWT iat claim in GitHub's future

The same code, the same key, the same App. On a laptop it works every time. In the container it returns 401 {"message": "'Issued at' claim ('iat') must be an Integer representing the time that the assertion was issued"}, and on the third host it works again until Tuesday. Nothing about the JWT changed between those runs. What changed is which machine did the signing.

**Full guide with diagrams:** https://www.allanninal.dev/github/jwt-clock-drift-iat/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_clock_skew.py
node node/github-clock-skew.mjs
```

## Test it

```bash
pytest python/test_github_clock_skew.py
node --test node/github-clock-skew.test.mjs
```
