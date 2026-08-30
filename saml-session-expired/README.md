# The SAML session lapsed and the authorization went with it

Nobody touched anything. The nightly job ran for six weeks, and this morning it returned 403 on every organization call with a message about SAML enforcement. Somebody logs into GitHub in a browser to check, comes back, reruns the job, and it works. The ticket gets closed as transient. Eight days later it happens again, at a different hour, and the second ticket says the API is flaky. It is not flaky and it is not random: the authorization that credential holds against the organization has an expiry date, the organization set the interval, and the browser login that &ldquo;fixed&rdquo; it was a person quietly renewing something they did not know they were renewing.

**Full guide with diagrams:** https://www.allanninal.dev/github/saml-session-expired/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_sso_session_clock.py
node node/github-sso-session-clock.mjs
```

## Test it

```bash
pytest python/test_github_sso_session_clock.py
node --test node/github-sso-session-clock.test.mjs
```
