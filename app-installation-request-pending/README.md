# The App installation was requested and never approved

The customer says they installed the App. Your product agrees: the connection screen is green, the account is on the list, the onboarding checklist is ticked. And nothing has ever arrived — no webhook deliveries, no repositories, no first sync — and every support thread ends with somebody asking them to uninstall and install it again, which they cannot do either, because they never installed it in the first place. They asked for it. They were not an owner of the organization, so the install button quietly became a request, and it is still sitting in a queue behind somebody who has never been told it is there.

**Full guide with diagrams:** https://www.allanninal.dev/github/app-installation-request-pending/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_app_installation_pending.py
node node/github-app-installation-pending.mjs
```

## Test it

```bash
pytest python/test_github_app_installation_pending.py
node --test node/github-app-installation-pending.test.mjs
```
