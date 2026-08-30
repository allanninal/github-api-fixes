# A fine-grained token that is waiting for an org owner

The token was created this morning. The permissions were ticked carefully against the documentation, the resource owner was set to the organization, and the settings page shows it exists with everything it needs. GET /user returns 200. Reads against your own repositories return 200. Every single call that touches the organization returns 403, or 404, and the permission the endpoint names is one the token visibly holds. Nothing is missing. The token is sitting in a queue waiting for an organization owner to approve it, and the API has no interest in telling you that.

**Full guide with diagrams:** https://www.allanninal.dev/github/fine-grained-pat-pending-approval/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_pat_pending_approval.py
node node/github-pat-pending-approval.mjs
```

## Test it

```bash
pytest python/test_github_pat_pending_approval.py
node --test node/github-pat-pending-approval.test.mjs
```
