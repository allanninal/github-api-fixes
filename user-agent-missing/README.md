# every request 403s because there is no User-Agent header

403 {&quot;message&quot;:&quot;Request forbidden by administrative rules. Please make sure your request has a User-Agent header.&quot;} &mdash; and it happens on the REST root, which any anonymous caller can read. Nobody looks at the body, because 403 in this API almost always means quota or permissions, and both of those theories send you somewhere the answer is not.

**Full guide with diagrams:** https://www.allanninal.dev/github/user-agent-missing/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_user_agent_403.py
node node/github-user-agent-403.mjs
```

## Test it

```bash
pytest python/test_github_user_agent_403.py
node --test node/github-user-agent-403.test.mjs
```
