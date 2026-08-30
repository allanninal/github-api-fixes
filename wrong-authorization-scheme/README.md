# a JWT sent as token, and the 401 blames the credential

The App is registered, the private key is the right one, the JWT is freshly signed and it verifies in three different debuggers. GitHub answers 401 {&quot;message&quot;:&quot;Bad credentials&quot;}. The credential in that request is perfect. The word in front of it is not, and the message never mentions words.

**Full guide with diagrams:** https://www.allanninal.dev/github/wrong-authorization-scheme/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_auth_scheme.py
node node/github-auth-scheme.mjs
```

## Test it

```bash
pytest python/test_github_auth_scheme.py
node --test node/github-auth-scheme.test.mjs
```
