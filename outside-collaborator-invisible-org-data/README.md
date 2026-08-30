# An outside collaborator has repos in an org, not the org

The integration reads three repositories in the customer's organization perfectly well, and has done for months. Then somebody adds a feature that needs the team list, and GET /orgs/{org}/teams returns 404. Which is odd, because the repositories are right there and they belong to that organization. So the token gets read:org added, and it is still 404. Then admin:org, briefly, against everybody's better judgement, and it is still 404. The account was never an organization member. It is an outside collaborator, which means it has repositories inside the organization and no standing in the organization, and no scope on earth grants standing.

**Full guide with diagrams:** https://www.allanninal.dev/github/outside-collaborator-invisible-org-data/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_outside_collaborator.py
node node/github-outside-collaborator.mjs
```

## Test it

```bash
pytest python/test_github_outside_collaborator.py
node --test node/github-outside-collaborator.test.mjs
```
