# the App JWT is signed with the wrong key or algorithm

It worked until the key was rotated. Now every call is 401 {"message": "A JSON web token could not be decoded"}, or occasionally 404 {"message": "Integration not found"}, and the two arrive from what looks like the same deployment. The key is in the environment. The App exists. Somebody has already pasted the PEM into three different terminals to check it looks right.

**Full guide with diagrams:** https://www.allanninal.dev/github/jwt-wrong-key-or-algorithm/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_app_key_identity.py
node node/github-app-key-identity.mjs
```

## Test it

```bash
pytest python/test_github_app_key_identity.py
node --test node/github-app-key-identity.test.mjs
```
