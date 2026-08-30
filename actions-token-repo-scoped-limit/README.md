# GITHUB_TOKEN gets 1,000 an hour, shared across the repo

The script works on your laptop. It makes about 1,400 calls, finishes in two minutes, and nobody thinks about it again until it is moved into a workflow. There it gets to roughly call 1,000 and starts collecting 403. Same code, same repository, same endpoints. The only thing that changed is which credential is in the environment, and that credential was handed a ceiling a fifth the size of the one you tested against.

**Full guide with diagrams:** https://www.allanninal.dev/github/actions-token-repo-scoped-limit/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_actions_token_budget.py
node node/github-actions-token-budget.mjs
```

## Test it

```bash
pytest python/test_github_actions_token_budget.py
node --test node/github-actions-token-budget.test.mjs
```
