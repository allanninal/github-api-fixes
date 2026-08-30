# The client is pointed at the wrong GitHub host entirely

The code is shared between the hosted product and the customer's own installation, and it works in one of them. In the other every endpoint answers 404, which sends everybody hunting for a permission, or the token gets a flat 401 Bad credentials despite being minted twenty minutes ago and pasted straight in. Both symptoms have the same cause and it is not on the list anybody checks: the client is talking to a different GitHub installation from the one that holds the resources. An environment variable did not get set, or an SDK fell back to its built-in default, and the requests are going somewhere the token means nothing and the repositories do not exist.

**Full guide with diagrams:** https://www.allanninal.dev/github/enterprise-endpoint-on-dotcom/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_api_host.py
node node/github-api-host.mjs
```

## Test it

```bash
pytest python/test_github_api_host.py
node --test node/github-api-host.test.mjs
```
