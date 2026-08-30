# Enforcing 2FA removed the machine account from the org

On Tuesday the sync job read forty repositories. On Wednesday it reads none of them, and every call comes back 404 rather than 403. The token is checked first, because it always is: GET /user answers 200, the login is right, the expiry is months away, the scopes are unchanged. Nothing was rotated and nothing was deployed. What happened is that an owner turned on required two-factor authentication, and the accounts that did not have 2FA were removed from the organization — which for a machine account created in a hurry three years ago means all of them.

**Full guide with diagrams:** https://www.allanninal.dev/github/org-2fa-requirement-removed-member/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_org_membership_lost.py
node node/github-org-membership-lost.mjs
```

## Test it

```bash
pytest python/test_github_org_membership_lost.py
node --test node/github-org-membership-lost.test.mjs
```
