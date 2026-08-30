# bulk issue or comment creation exceeds 80 requests a minute

The migration script imports 2,400 issues from the old tracker. It gets through about eighty of them, then every remaining call comes back 403. You check the quota and there are 4,900 requests left in it. The limit that stopped you is not counting requests. It is counting the things you created.

**Full guide with diagrams:** https://www.allanninal.dev/github/secondary-limit-content-creation/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_content_burst_audit.py
node node/github-content-burst-audit.mjs
```

## Test it

```bash
pytest python/test_github_content_burst_audit.py
node --test node/github-content-burst-audit.test.mjs
```
