# a permission error is disguised as 404 Not Found

The repository is open in a browser tab in front of you. The script asks for the same repository and gets 404 {"message":"Not Found"}. Somebody checks the spelling, then the owner name, then the case of both, then writes a ticket saying the API is broken. The API is not broken. It is refusing to tell you that the repository exists, because telling you would leak the existence of a private repository to a credential that has no business knowing about it.

**Full guide with diagrams:** https://www.allanninal.dev/github/404-masking-403/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_404_triage.py
node node/github-404-triage.mjs
```

## Test it

```bash
pytest python/test_github_404_triage.py
node --test node/github-404-triage.test.mjs
```
