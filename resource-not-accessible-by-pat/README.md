# 403 Resource not accessible by personal access token

The token was minted this morning with the permissions somebody carefully ticked, it reads the repository perfectly well, and one call comes back 403 {&quot;message&quot;: &quot;Resource not accessible by personal access token&quot;}. The obvious next move is to look at what the token holds and compare it against what the endpoint wanted. Half of that is possible. The endpoint will tell you what it wanted. Nothing anywhere will tell you what the token holds.

**Full guide with diagrams:** https://www.allanninal.dev/github/resource-not-accessible-by-pat/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_fine_grained_pat_probe.py
node node/github-fine-grained-pat-probe.mjs
```

## Test it

```bash
pytest python/test_github_fine_grained_pat_probe.py
node --test node/github-fine-grained-pat-probe.test.mjs
```
