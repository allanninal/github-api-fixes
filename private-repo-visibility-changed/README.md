# A repository went private and anonymous callers now see 404

The dependency dashboard has read one repository's tags and release notes since 2019, anonymously, because it is a public repository and there was never any reason to authenticate. On a Tuesday it starts returning 404. Nothing was deployed, no code changed, the URL is character-for-character the one that worked on Monday. Somebody checks whether the repository was deleted, finds it in their browser because their browser is logged in, and now has two contradictory facts and no idea which to believe.

**Full guide with diagrams:** https://www.allanninal.dev/github/private-repo-visibility-changed/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_visibility_change.py
node node/github-visibility-change.mjs
```

## Test it

```bash
pytest python/test_github_visibility_change.py
node --test node/github-visibility-change.test.mjs
```
