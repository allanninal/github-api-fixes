# the client still sends a username and password to the API

401 {"message": "Support for password authentication was removed. Please use a personal access token instead."}. The reflex is to go and mint a token, which is right, and then to paste it where the password was, which is half right, and then to be surprised when a different call still fails. The credential was never the problem. The envelope was.

**Full guide with diagrams:** https://www.allanninal.dev/github/basic-auth-password-removed/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_auth_scheme_check.py
node node/github-auth-scheme-check.mjs
```

## Test it

```bash
pytest python/test_github_auth_scheme_check.py
node --test node/github-auth-scheme-check.test.mjs
```
