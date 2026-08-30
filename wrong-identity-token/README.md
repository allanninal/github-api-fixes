# the automation runs as a person who can leave the company

The release notes are signed by someone who left in March. Every automated review comment carries a colleague's avatar, every bot commit says their name, and none of it is a display problem: the token doing the work was minted on their account four years ago, so the integration is not running as a service. It is running as them.

**Full guide with diagrams:** https://www.allanninal.dev/github/wrong-identity-token/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_actor_identity.py
node node/github-actor-identity.mjs
```

## Test it

```bash
pytest python/test_github_actor_identity.py
node --test node/github-actor-identity.test.mjs
```
