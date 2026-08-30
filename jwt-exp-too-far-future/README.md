# a GitHub App JWT that expires in an hour is refused

The private key is right, the App exists, the signature verifies and the request still comes back 401 {"message": "'Expiration time' claim ('exp') is too far in the future"}. Three people regenerate the key. Somebody rewrites the signing code in a different library. The defect is one number, chosen three lines above the request, and it was wrong before anything was sent.

**Full guide with diagrams:** https://www.allanninal.dev/github/jwt-exp-too-far-future/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_app_jwt_claims.py
node node/github-app-jwt-claims.mjs
```

## Test it

```bash
pytest python/test_github_app_jwt_claims.py
node --test node/github-app-jwt-claims.test.mjs
```
