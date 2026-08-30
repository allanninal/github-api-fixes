# GitHub answers 404 for the wrong verb, never 405

The path was copied out of the documentation. It is on the page, in a code block, with the owner and the repository substituted in correctly, and it comes back 404 Not Found. So the search starts where every 404 search starts: is the repository private, is the token scoped, is the App installed. All of those come back fine, which is confusing, and somebody eventually widens the token to something alarming just to see. Still 404. The path was never the problem and neither was the credential. The endpoint does not accept the method that was sent, and GitHub has documented that it will tell you so with a 404 rather than a 405.

**Full guide with diagrams:** https://www.allanninal.dev/github/missing-endpoint-404-vs-405/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_route_or_verb.py
node node/github-route-or-verb.mjs
```

## Test it

```bash
pytest python/test_github_route_or_verb.py
node --test node/github-route-or-verb.test.mjs
```
