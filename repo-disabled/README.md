# the repository is disabled and behaves like a ghost

The weekly platform report says the organisation has one repository with no branch protection, no webhooks, no open pull requests and no contributors. It is not a new repository and it is not empty; it shipped for three years. Every call the report makes against it either 404s or comes back with nothing in it, while the repository itself reads fine and sits in the organisation listing next to everything else. One boolean on the repository object explains all of it, and it is not archived.

**Full guide with diagrams:** https://www.allanninal.dev/github/repo-disabled/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_disabled_repo_probe.py
node node/github-disabled-repo-probe.mjs
```

## Test it

```bash
pytest python/test_github_disabled_repo_probe.py
node --test node/github-disabled-repo-probe.test.mjs
```
