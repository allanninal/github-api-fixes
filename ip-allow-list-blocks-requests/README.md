# The org's IP allow list refuses the runner, not the token

The job fails in CI and passes on the laptop. Same repository, same endpoint, same token — the engineer copies the token out of the secret store and runs the identical command locally to prove it, and it works. So the token is fine, and the code is fine, and the only thing left is the API, which is up. Meanwhile anonymous calls to the organization's public repositories keep answering 200 from the same runner, which is taken as proof that the network is fine too. It is not the token and not the network. The organization restricts which source addresses may reach it, and the runner's address has never been on the list.

**Full guide with diagrams:** https://www.allanninal.dev/github/ip-allow-list-blocks-requests/

## Run it

```bash
export DRY_RUN="true"   # report only, write nothing
python python/github_ip_allow_list.py
node node/github-ip-allow-list.mjs
```

## Test it

```bash
pytest python/test_github_ip_allow_list.py
node --test node/github-ip-allow-list.test.mjs
```
