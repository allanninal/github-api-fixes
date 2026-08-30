# the deploy key is read-only and the push needs write

The pipeline has cloned this repository twice a day for eighteen months. Somebody adds a step that pushes a version bump back, and it fails with ERROR: The key you are authenticating with has been marked as read only. &mdash; from Git, over SSH, with no HTTP status and nothing in the API logs. So the investigation starts in the wrong tool, goes through the known-hosts file and the agent and the private key in the secret store, and none of that is where the answer is. The answer is a boolean on an object the API will hand you in one request.

**Full guide with diagrams:** https://www.allanninal.dev/github/deploy-key-read-only-assumed-write/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_deploy_key_capability.py
node node/github-deploy-key-capability.mjs
```

## Test it

```bash
pytest python/test_github_deploy_key_capability.py
node --test node/github-deploy-key-capability.test.mjs
```
