# The org restricts OAuth Apps and this one was never approved

The integration works. Customers sign in, it reads their repositories, and the reviews are good. Then one customer files a ticket saying it shows them nothing, and their logs show 403 on every call that touches their organization while the calls against their personal repositories return happily. The token is fine. The scopes are the same ones every other customer granted. The account is a member of the organization and can see all of it in a browser. What is different is a setting neither of you can see from where you are standing: the organization has decided which OAuth Apps may touch its data, and yours is not on the list.

**Full guide with diagrams:** https://www.allanninal.dev/github/oauth-app-access-restricted/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_oauth_app_restriction.py
node node/github-oauth-app-restriction.mjs
```

## Test it

```bash
pytest python/test_github_oauth_app_restriction.py
node --test node/github-oauth-app-restriction.test.mjs
```
