# org lists silently omit SSO-enforced organizations

The user belongs to six organizations. GET /user/orgs returns four. Status 200, valid JSON, no errors array, nothing in the body that hints anything is absent. The two organizations that enforce SAML single sign-on for a token that was never authorized against them are simply not in the list, and the only place GitHub mentions it is a response header called X-GitHub-SSO.

**Full guide with diagrams:** https://www.allanninal.dev/github/saml-partial-results/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_sso_partial_results.py
node node/github-sso-partial-results.mjs
```

## Test it

```bash
pytest python/test_github_sso_partial_results.py
node --test node/github-sso-partial-results.test.mjs
```
