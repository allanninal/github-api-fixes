# a read-only job holds a token that can delete repositories

There is no incident to attach this to. The job runs every ten minutes, lists open pull requests, posts a count to a dashboard and has not failed once this year. The token it uses was created in four seconds by ticking repo, and it can force-push to every private repository in the organization, rewrite webhooks and delete the lot.

**Full guide with diagrams:** https://www.allanninal.dev/github/over-scoped-token/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_scope_blast_radius.py
node node/github-scope-blast-radius.mjs
```

## Test it

```bash
pytest python/test_github_scope_blast_radius.py
node --test node/github-scope-blast-radius.test.mjs
```
