# The org's base permission dropped and repos vanished

The inventory job used to report on four hundred repositories. This morning it reported on nine, and reported them cheerfully: no errors, no refusals, no retries, a clean run in a tenth of the usual time. Nothing was revoked from the account — it is still a member of the organization, its token is unchanged, and the nine repositories it can see are ones somebody added it to explicitly, years ago. What changed is one field on the organization. Somebody moved the base permission from read to none, which is a good and normal thing to do, and every member's implicit access to every repository they were never explicitly added to ended in the same instant.

**Full guide with diagrams:** https://www.allanninal.dev/github/org-base-permission-changed/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_org_base_permission.py
node node/github-org-base-permission.mjs
```

## Test it

```bash
pytest python/test_github_org_base_permission.py
node --test node/github-org-base-permission.test.mjs
```
