# a hardcoded installation id stops matching reality

The installation id was copied out of a settings URL in 2024 and pasted into an environment variable, where it has sat ever since. Last Tuesday an admin at one customer uninstalled the App during a security review and put it straight back, which they were entitled to do and which nobody told you about. Since then the token call for that customer has returned 404, and the error says nothing about installations.

**Full guide with diagrams:** https://www.allanninal.dev/github/app-installation-id-hardcoded/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_installation_id_drift.py
node node/github-installation-id-drift.mjs
```

## Test it

```bash
pytest python/test_github_installation_id_drift.py
node --test node/github-installation-id-drift.test.mjs
```
