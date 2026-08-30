# GitHub API Fixes

Read-only Python and Node.js scripts that find GitHub API problems — pagination that stops at the first page, 404s masking 403s, webhooks failing unnoticed and Apps missing a permission. They report and print the repair; they never write.

Every script here is read only. They hold a credential to a live account, so none of them writes: each one reads through the API, reports exactly what is wrong, and prints the repair for you to run.

By **[Allan Niñal](https://github.com/allanninal)** — AI Solutions Engineer. I build AI powered tools, data products, and AWS automation.
Full write ups with diagrams for each fix live at **[allanninal.dev/github](https://www.allanninal.dev/github/)**.

[![Follow on GitHub](https://img.shields.io/github/followers/allanninal?label=Follow%20%40allanninal&style=social)](https://github.com/allanninal)
## The fixes

- [a permission error is disguised as 404 Not Found](./404-masking-403/) — https://www.allanninal.dev/github/404-masking-403/
- [GITHUB_TOKEN gets 1,000 an hour, shared across the repo](./actions-token-repo-scoped-limit/) — https://www.allanninal.dev/github/actions-token-repo-scoped-limit/
- [a hardcoded installation id stops matching reality](./app-installation-id-hardcoded/) — https://www.allanninal.dev/github/app-installation-id-hardcoded/
- [a 404 that means the App is not installed on that repo](./app-not-installed-on-repo/) — https://www.allanninal.dev/github/app-not-installed-on-repo/
- [the App was never subscribed to the event it waits for](./app-not-subscribed-to-event/) — https://www.allanninal.dev/github/app-not-subscribed-to-event/
- [resource not accessible by integration on one endpoint](./app-permission-missing/) — https://www.allanninal.dev/github/app-permission-missing/
- [a new App permission that installers never accepted](./app-permission-upgrade-not-accepted/) — https://www.allanninal.dev/github/app-permission-upgrade-not-accepted/
- [the App's rate limit never grew with the installation](./app-rate-limit-not-scaling/) — https://www.allanninal.dev/github/app-rate-limit-not-scaling/
- [the installation token was narrowed below what the job needs](./app-token-scoped-down-too-far/) — https://www.allanninal.dev/github/app-token-scoped-down-too-far/
- [the GitHub App has no webhook URL configured](./app-webhook-url-unset/) — https://www.allanninal.dev/github/app-webhook-url-unset/
- [401 Bad credentials on every endpoint, even public ones](./bad-credentials-401/) — https://www.allanninal.dev/github/bad-credentials-401/
- [the client still sends a username and password to the API](./basic-auth-password-removed/) — https://www.allanninal.dev/github/basic-auth-password-removed/
- [a classic PAT passed its expiry and everything broke at once](./classic-pat-expired/) — https://www.allanninal.dev/github/classic-pat-expired/
- [code search is billed to its own 10 a minute bucket](./code-search-bucket-exhausted/) — https://www.allanninal.dev/github/code-search-bucket-exhausted/
- [the compare endpoint stops at 250 commits and says nothing](./compare-250-commit-cap/) — https://www.allanninal.dev/github/compare-250-commit-cap/
- [the same webhook URL is registered on the org and the repo](./duplicate-webhooks/) — https://www.allanninal.dev/github/duplicate-webhooks/
- [the endpoint ignores page and returns page one forever](./endpoint-ignores-page-param/) — https://www.allanninal.dev/github/endpoint-ignores-page-param/
- [rotating the token invalidates every cached ETag at once](./etag-invalidated-by-token-rotation/) — https://www.allanninal.dev/github/etag-invalidated-by-token-rotation/
- [GraphQL returns 200 with an errors array and null data](./graphql-200-with-errors/) — https://www.allanninal.dev/github/graphql-200-with-errors/
- [A nested GraphQL query requests more than 500,000 nodes](./graphql-node-limit-exceeded/) — https://www.allanninal.dev/github/graphql-node-limit-exceeded/
- [GraphQL data is present but individual fields are null](./graphql-partial-data-nulls/) — https://www.allanninal.dev/github/graphql-partial-data-nulls/
- [GraphQL points run out in a bucket separate from REST](./graphql-rate-limited/) — https://www.allanninal.dev/github/graphql-rate-limited/
- [the installation covers only some repositories, silently](./installation-repository-selection-partial/) — https://www.allanninal.dev/github/installation-repository-selection-partial/
- [the installation is suspended and every call it makes 403s](./installation-suspended/) — https://www.allanninal.dev/github/installation-suspended/
- [the installation token expired an hour into the job](./installation-token-expired/) — https://www.allanninal.dev/github/installation-token-expired/
- [some endpoints refuse an installation token whatever it holds](./installation-token-rejected-by-endpoint/) — https://www.allanninal.dev/github/installation-token-rejected-by-endpoint/
- [clock drift puts the JWT iat claim in GitHub's future](./jwt-clock-drift-iat/) — https://www.allanninal.dev/github/jwt-clock-drift-iat/
- [a GitHub App JWT that expires in an hour is refused](./jwt-exp-too-far-future/) — https://www.allanninal.dev/github/jwt-exp-too-far-future/
- [the App JWT is signed with the wrong key or algorithm](./jwt-wrong-key-or-algorithm/) — https://www.allanninal.dev/github/jwt-wrong-key-or-algorithm/
- [only the first page is read because the Link header is ignored](./link-header-not-followed/) — https://www.allanninal.dev/github/link-header-not-followed/
- [the endpoint accepts a scope your token was never given](./missing-oauth-scope/) — https://www.allanninal.dev/github/missing-oauth-scope/
- [polling without ETags spends full quota on unchanged data](./no-conditional-requests/) — https://www.allanninal.dev/github/no-conditional-requests/
- [one user revoked your app and only their token is dead](./oauth-token-revoked-by-user/) — https://www.allanninal.dev/github/oauth-token-revoked-by-user/
- [a read-only job holds a token that can delete repositories](./over-scoped-token/) — https://www.allanninal.dev/github/over-scoped-token/
- [per_page is unset so every list costs 3.3x more requests](./per-page-default-30/) — https://www.allanninal.dev/github/per-page-default-30/
- [per_page above 100 is clamped and never rejected](./per-page-over-100-clamped/) — https://www.allanninal.dev/github/per-page-over-100-clamped/
- [the x-poll-interval header is ignored on events endpoints](./poll-interval-header-ignored/) — https://www.allanninal.dev/github/poll-interval-header-ignored/
- [the integration polls for events a webhook would push](./polling-instead-of-webhooks/) — https://www.allanninal.dev/github/polling-instead-of-webhooks/
- [a pull request's files and commits lists are both capped](./pr-files-and-commits-caps/) — https://www.allanninal.dev/github/pr-files-and-commits-caps/
- [core REST quota is exhausted and every call returns 403](./rate-limit-core-exhausted/) — https://www.allanninal.dev/github/rate-limit-core-exhausted/
- [requests go out anonymous and are capped at 60 an hour](./rate-limit-unauthenticated/) — https://www.allanninal.dev/github/rate-limit-unauthenticated/
- [the Link header has no rel=last so the page count breaks](./rel-last-absent/) — https://www.allanninal.dev/github/rel-last-absent/
- [the repository was renamed and every call now 301s](./repo-renamed-301-redirect/) — https://www.allanninal.dev/github/repo-renamed-301-redirect/
- [expensive requests are killed at ten seconds with a 502](./request-timeout-502/) — https://www.allanninal.dev/github/request-timeout-502/
- [the client ignores retry-after and keeps hammering the API](./retry-after-ignored/) — https://www.allanninal.dev/github/retry-after-ignored/
- [org lists silently omit SSO-enforced organizations](./saml-partial-results/) — https://www.allanninal.dev/github/saml-partial-results/
- [search returns at most 1,000 results whatever total_count says](./search-1000-result-cap/) — https://www.allanninal.dev/github/search-1000-result-cap/
- [search has its own 30-per-minute bucket and drains separately](./search-bucket-exhausted/) — https://www.allanninal.dev/github/search-bucket-exhausted/
- [incomplete_results is true and the search answer is partial](./search-incomplete-results/) — https://www.allanninal.dev/github/search-incomplete-results/
- [over 100 concurrent requests trips a secondary rate limit](./secondary-limit-concurrency/) — https://www.allanninal.dev/github/secondary-limit-concurrency/
- [bulk issue or comment creation exceeds 80 requests a minute](./secondary-limit-content-creation/) — https://www.allanninal.dev/github/secondary-limit-content-creation/
- [a hot endpoint burns 900 points a minute and gets throttled](./secondary-limit-points-per-minute/) — https://www.allanninal.dev/github/secondary-limit-points-per-minute/
- [the token expires in days and nothing is watching the clock](./token-expiring-soon/) — https://www.allanninal.dev/github/token-expiring-soon/
- [the token is passed as an access_token query parameter](./token-in-query-string/) — https://www.allanninal.dev/github/token-in-query-string/
- [rows move between pages and the walk skips records](./unstable-sort-duplicates/) — https://www.allanninal.dev/github/unstable-sort-duplicates/
- [a pinned X-GitHub-Api-Version stopped being supported](./unsupported-api-version/) — https://www.allanninal.dev/github/unsupported-api-version/
- [a classic token nobody used for a year is deleted for you](./unused-classic-token-auto-revoked/) — https://www.allanninal.dev/github/unused-classic-token-auto-revoked/
- [every request 403s because there is no User-Agent header](./user-agent-missing/) — https://www.allanninal.dev/github/user-agent-missing/
- [the hook sends form-encoded bodies to a JSON receiver](./webhook-content-type-mismatch/) — https://www.allanninal.dev/github/webhook-content-type-mismatch/
- [webhook deliveries are failing and nobody reads the log](./webhook-deliveries-failing/) — https://www.allanninal.dev/github/webhook-deliveries-failing/
- [the hook is not subscribed to the event you are waiting for](./webhook-event-not-subscribed/) — https://www.allanninal.dev/github/webhook-event-not-subscribed/
- [the webhook posts your payloads to an http:// URL](./webhook-http-url/) — https://www.allanninal.dev/github/webhook-http-url/
- [the webhook exists but somebody switched it off](./webhook-inactive/) — https://www.allanninal.dev/github/webhook-inactive/
- [SSL verification is switched off on the webhook](./webhook-insecure-ssl/) — https://www.allanninal.dev/github/webhook-insecure-ssl/
- [a firewall allow-list no longer matches GitHub's hook IPs](./webhook-ip-allowlist-drift/) — https://www.allanninal.dev/github/webhook-ip-allowlist-drift/
- [a webhook with no secret sends no signature to verify](./webhook-no-secret/) — https://www.allanninal.dev/github/webhook-no-secret/
- [the webhook secret is set and has never been rotated](./webhook-secret-never-rotated/) — https://www.allanninal.dev/github/webhook-secret-never-rotated/
- [the receiver still checks the legacy SHA-1 signature](./webhook-sha1-signature-only/) — https://www.allanninal.dev/github/webhook-sha1-signature-only/
- [the receiver takes longer than 10 seconds and times out](./webhook-timeout-10s/) — https://www.allanninal.dev/github/webhook-timeout-10s/
- [the hook subscribes to every event with a wildcard](./webhook-wildcard-events/) — https://www.allanninal.dev/github/webhook-wildcard-events/
- [a JWT sent as token, and the 401 blames the credential](./wrong-authorization-scheme/) — https://www.allanninal.dev/github/wrong-authorization-scheme/
- [the automation runs as a person who can leave the company](./wrong-identity-token/) — https://www.allanninal.dev/github/wrong-identity-token/

## How to run one

Each folder holds the same script in Python and in Node.js, plus its test. Set the environment variables named in that folder's README and run it. Nothing writes, so there is no dry run to enable and no flag to be careful about — use a restricted, read-only credential and the worst case is that it tells you nothing is wrong.

## License

MIT. Use it, change it, ship it.
