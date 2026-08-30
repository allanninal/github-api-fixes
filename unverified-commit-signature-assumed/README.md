# The signature audit reads verified and never reads reason

The compliance answer says every commit on the release branch is signed. Somebody wrote a script to prove it, the script has been green for eighteen months, and nobody has looked at it since. Then an auditor asks how the check works and the answer is that it walks the commit list and confirms every author is on the approved list of engineers. That is not a signature check. The commit author is a string the committing client sets, it is never authenticated, and a script that reads it is checking that the person who pushed knew how to type a colleague's name. The signature result is right there in the same response, on a field the script never opens.

**Full guide with diagrams:** https://www.allanninal.dev/github/unverified-commit-signature-assumed/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_commit_signatures.py
node node/github-commit-signatures.mjs
```

## Test it

```bash
pytest python/test_github_commit_signatures.py
node --test node/github-commit-signatures.test.mjs
```
