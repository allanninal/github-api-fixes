# a classic token nobody used for a year is deleted for you

The restore drill is on a Tuesday morning and the token that has been sitting in the vault since the runbook was written answers 401. It has not expired, because it was created with no expiry. Nobody revoked it. It is not in the account's token list at all, because GitHub removed it for going a year without being used, and the reason it went a year without being used is that it is a token for emergencies.

**Full guide with diagrams:** https://www.allanninal.dev/github/unused-classic-token-auto-revoked/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_token_dormancy.py
node node/github-token-dormancy.mjs
```

## Test it

```bash
pytest python/test_github_token_dormancy.py
node --test node/github-token-dormancy.test.mjs
```
