# 401 Bad credentials on every endpoint, even public ones

401 {&quot;message&quot;:&quot;Bad credentials&quot;} on every endpoint you try, including ones that need no credential at all. The token is right there in the environment, it was working yesterday, and the error names nothing: not the account, not the scope, not which of the six things that produce this message actually happened.

**Full guide with diagrams:** https://www.allanninal.dev/github/bad-credentials-401/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_401_provenance.py
node node/github-401-provenance.mjs
```

## Test it

```bash
pytest python/test_github_401_provenance.py
node --test node/github-401-provenance.test.mjs
```
