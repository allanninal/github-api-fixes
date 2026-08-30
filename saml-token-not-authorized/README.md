# The token is valid and was never SSO-authorized for the org

The token was minted ten minutes ago with every scope the endpoint documents. GET /user works. GET /orgs/acme works. GET /orgs/acme/repos comes back 403 {"message": "Resource protected by organization SAML enforcement. You must grant your OAuth token access to this organization."}, and on some endpoints it does not even say that much — it returns a bare 404 on a repository that is open in the next browser tab. Nothing is wrong with the credential. The organization enforces SAML single sign-on, which means every token has to be individually authorized against it by a person, in a browser, and this one never has been.

**Full guide with diagrams:** https://www.allanninal.dev/github/saml-token-not-authorized/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_sso_required.py
node node/github-sso-required.mjs
```

## Test it

```bash
pytest python/test_github_sso_required.py
node --test node/github-sso-required.test.mjs
```
