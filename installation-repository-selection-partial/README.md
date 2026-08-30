# the installation covers only some repositories, silently

The scanner reports clean. Every repository it looked at was fine, every check passed, and the summary at the bottom says so. It looked at twelve repositories. The organization has a hundred and forty. Nothing errored, nothing warned, and no line of that report is untrue &mdash; the App installation was set to selected repositories at some point in 2023 and the other hundred and twenty-eight have never been inside its field of view.

**Full guide with diagrams:** https://www.allanninal.dev/github/installation-repository-selection-partial/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_app_coverage_audit.py
node node/github-app-coverage-audit.mjs
```

## Test it

```bash
pytest python/test_github_app_coverage_audit.py
node --test node/github-app-coverage-audit.test.mjs
```
