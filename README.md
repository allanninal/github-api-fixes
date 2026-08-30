# GitHub API Fixes

Read-only Python and Node.js scripts that find GitHub API problems — pagination that stops at the first page, 404s masking 403s, webhooks failing unnoticed and Apps missing a permission. They report and print the repair; they never write.

Every script here is read only. They hold a credential to a live account, so none of them writes: each one reads through the API, reports exactly what is wrong, and prints the repair for you to run.

By **[Allan Niñal](https://github.com/allanninal)** — AI Solutions Engineer. I build AI powered tools, data products, and AWS automation.
Full write ups with diagrams for each fix live at **[allanninal.dev/github](https://www.allanninal.dev/github/)**.

[![Follow on GitHub](https://img.shields.io/github/followers/allanninal?label=Follow%20%40allanninal&style=social)](https://github.com/allanninal)
## The fixes

- [a permission error is disguised as 404 Not Found](./404-masking-403/) — https://www.allanninal.dev/github/404-masking-403/
- [GITHUB_TOKEN gets 1,000 an hour, shared across the repo](./actions-token-repo-scoped-limit/) — https://www.allanninal.dev/github/actions-token-repo-scoped-limit/
- [resource not accessible by integration on one endpoint](./app-permission-missing/) — https://www.allanninal.dev/github/app-permission-missing/
- [401 Bad credentials on every endpoint, even public ones](./bad-credentials-401/) — https://www.allanninal.dev/github/bad-credentials-401/
- [the client still sends a username and password to the API](./basic-auth-password-removed/) — https://www.allanninal.dev/github/basic-auth-password-removed/
- [a classic PAT passed its expiry and everything broke at once](./classic-pat-expired/) — https://www.allanninal.dev/github/classic-pat-expired/
- [code search is billed to its own 10 a minute bucket](./code-search-bucket-exhausted/) — https://www.allanninal.dev/github/code-search-bucket-exhausted/
- [the compare endpoint stops at 250 commits and says nothing](./compare-250-commit-cap/) — https://www.allanninal.dev/github/compare-250-commit-cap/
- [the same webhook URL is registered on the org and the repo](./duplicate-webhooks/) — https://www.allanninal.dev/github/duplicate-webhooks/
- [rotating the token invalidates every cached ETag at once](./etag-invalidated-by-token-rotation/) — https://www.allanninal.dev/github/etag-invalidated-by-token-rotation/
- [the installation covers only some repositories, silently](./installation-repository-selection-partial/) — https://www.allanninal.dev/github/installation-repository-selection-partial/
- [only the first page is read because the Link header is ignored](./link-header-not-followed/) — https://www.allanninal.dev/github/link-header-not-followed/
- [the endpoint accepts a scope your token was never given](./missing-oauth-scope/) — https://www.allanninal.dev/github/missing-oauth-scope/
- [polling without ETags spends full quota on unchanged data](./no-conditional-requests/) — https://www.allanninal.dev/github/no-conditional-requests/
- [a read-only job holds a token that can delete repositories](./over-scoped-token/) — https://www.allanninal.dev/github/over-scoped-token/
- [per_page is unset so every list costs 3.3x more requests](./per-page-default-30/) — https://www.allanninal.dev/github/per-page-default-30/
- [the x-poll-interval header is ignored on events endpoints](./poll-interval-header-ignored/) — https://www.allanninal.dev/github/poll-interval-header-ignored/
- [the integration polls for events a webhook would push](./polling-instead-of-webhooks/) — https://www.allanninal.dev/github/polling-instead-of-webhooks/
- [core REST quota is exhausted and every call returns 403](./rate-limit-core-exhausted/) — https://www.allanninal.dev/github/rate-limit-core-exhausted/
- [requests go out anonymous and are capped at 60 an hour](./rate-limit-unauthenticated/) — https://www.allanninal.dev/github/rate-limit-unauthenticated/
- [the client ignores retry-after and keeps hammering the API](./retry-after-ignored/) — https://www.allanninal.dev/github/retry-after-ignored/
- [org lists silently omit SSO-enforced organizations](./saml-partial-results/) — https://www.allanninal.dev/github/saml-partial-results/
- [search returns at most 1,000 results whatever total_count says](./search-1000-result-cap/) — https://www.allanninal.dev/github/search-1000-result-cap/
- [search has its own 30-per-minute bucket and drains separately](./search-bucket-exhausted/) — https://www.allanninal.dev/github/search-bucket-exhausted/
- [over 100 concurrent requests trips a secondary rate limit](./secondary-limit-concurrency/) — https://www.allanninal.dev/github/secondary-limit-concurrency/
- [bulk issue or comment creation exceeds 80 requests a minute](./secondary-limit-content-creation/) — https://www.allanninal.dev/github/secondary-limit-content-creation/
- [a hot endpoint burns 900 points a minute and gets throttled](./secondary-limit-points-per-minute/) — https://www.allanninal.dev/github/secondary-limit-points-per-minute/
- [the token expires in days and nothing is watching the clock](./token-expiring-soon/) — https://www.allanninal.dev/github/token-expiring-soon/
- [the token is passed as an access_token query parameter](./token-in-query-string/) — https://www.allanninal.dev/github/token-in-query-string/
- [webhook deliveries are failing and nobody reads the log](./webhook-deliveries-failing/) — https://www.allanninal.dev/github/webhook-deliveries-failing/
- [the hook is not subscribed to the event you are waiting for](./webhook-event-not-subscribed/) — https://www.allanninal.dev/github/webhook-event-not-subscribed/
- [a webhook with no secret sends no signature to verify](./webhook-no-secret/) — https://www.allanninal.dev/github/webhook-no-secret/

## How to run one

Each folder holds the same script in Python and in Node.js, plus its test. Set the environment variables named in that folder's README and run it. Nothing writes, so there is no dry run to enable and no flag to be careful about — use a restricted, read-only credential and the worst case is that it tells you nothing is wrong.

## License

MIT. Use it, change it, ship it.
