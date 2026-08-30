# branch protection is unreadable without admin, not absent

The compliance report says nought out of two hundred and twelve repositories have branch protection. The security lead opens three of them at random and every one has protection on main, with required reviews and required checks, exactly as the policy says. The script is not lying about what it saw. It asked for the protection rules with a token that has read access and no more, GitHub answered 403 Must have admin rights to Repository., and somewhere in a try block that refusal became protected = False.

**Full guide with diagrams:** https://www.allanninal.dev/github/branch-protection-requires-admin/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_branch_protection_audit.py
node node/github-branch-protection-audit.mjs
```

## Test it

```bash
pytest python/test_github_branch_protection_audit.py
node --test node/github-branch-protection-audit.test.mjs
```
