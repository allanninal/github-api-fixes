"""Find GitHub webhooks with no secret, and hooks whose secret is being rejected.

Read only. Every request is a GET. The script can prove a hook has no secret,
because the key is simply absent from config. It cannot prove a secret is
correct: the value comes back masked, so a wrong secret and a right one are
indistinguishable until deliveries start failing.
"""
import argparse
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_hook_secret_audit")

API = "https://api.github.com"
UA = "github-hook-secret-audit/1.0"

# What GitHub returns in place of a secret that is set. Its presence is the only
# positive signal available; its value carries no information at all.
MASK = "********"


def secret_state(hook):
    """Is a secret configured on this hook? Pure.

    GitHub masks a configured secret and omits the key when there is none, so
    absence is a real finding rather than an inference. Anything else about the
    secret, including whether it is the right one, is not knowable from here.
    """
    config = hook.get("config")
    if not isinstance(config, dict):
        return "unknown"
    if "secret" not in config:
        return "absent"
    value = config.get("secret")
    if value is None or str(value).strip() == "":
        return "absent"
    return "set"


def unauthorized(deliveries):
    """Count deliveries the receiver refused with 401 or 403. Pure.

    Returns (rejected, total). These are the responses your own server gave, so
    on a hook that has a secret they are the only visible trace of a mismatch
    between the value GitHub signs with and the value the receiver checks.
    """
    rejected = total = 0
    for d in deliveries or []:
        total += 1
        try:
            code = int(d.get("status_code"))
        except (TypeError, ValueError):
            continue
        if code in (401, 403):
            rejected += 1
    return rejected, total


def verdict(hook, rejected=0, delivered=0):
    """Classify one hook. Pure, so the asymmetry is visible and testable.

    Returns (state, detail). "unsigned" is a fact about the configuration.
    "signed" is the absence of evidence and says so.
    """
    state = secret_state(hook)
    url = (hook.get("config") or {}).get("url") or "the configured URL"

    if state == "unknown":
        return ("unknown", "no config on this hook, which should not happen; "
                           "re-read it with GET /repos/{owner}/{repo}/hooks/{id}")

    if state == "absent":
        return ("unsigned",
                "config has no secret key, so GitHub sends no X-Hub-Signature-256 "
                "header with these payloads. A receiver that verifies only when "
                "the header is present verifies nothing, and anyone who learns %s "
                "can post to it." % url)

    if rejected and delivered and rejected * 2 >= delivered:
        return ("rejected",
                "a secret is set and %d of %d recent deliveries came back 401 or "
                "403 from your server. That is what a mismatched secret looks "
                "like from here; the value itself is masked and cannot be "
                "compared." % (rejected, delivered))

    detail = ("a secret is set, so payloads are signed. The value is masked as "
              "%s, so this says nothing about whether it matches the one your "
              "receiver holds." % MASK)
    if rejected:
        detail += (" %d of %d recent deliveries were refused with 401 or 403, "
                   "which is worth reading before you trust it."
                   % (rejected, delivered))
    return ("signed", detail)


def next_link(response):
    """The rel=next URL from the Link header, or None."""
    for part in (response.headers.get("Link") or "").split(","):
        chunk = part.strip()
        if chunk.startswith("<") and chunk.endswith('rel="next"'):
            return chunk[1:chunk.index(">")]
    return None


def get(session, url, **params):
    r = session.get(url, params=params, timeout=30)
    if r.status_code == 401:
        raise SystemExit("401 from GitHub: GITHUB_TOKEN is missing, expired or "
                         "malformed")
    if r.status_code in (403, 404):
        raise SystemExit("%d from %s: listing hooks needs admin:repo_hook for a "
                         "repository or admin:org_hook for an organization"
                         % (r.status_code, url))
    r.raise_for_status()
    return r


def page(session, url, limit=500, **params):
    out = []
    while url and len(out) < limit:
        r = get(session, url, **params)
        out.extend(r.json())
        url, params = next_link(r), {}
    return out[:limit]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", action="append", default=[],
                    help="owner/name; repeat for several repositories")
    ap.add_argument("--org", action="append", default=[],
                    help="organization login; repeat for several orgs")
    ap.add_argument("--max-deliveries", type=int, default=50,
                    help="deliveries to read per hook when looking for 401s "
                         "(0 to skip that read entirely)")
    args = ap.parse_args()

    if not (args.repo or args.org):
        log.error("pass at least one --repo owner/name or --org login")
        return 2

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN (a read-only token is enough)")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    scopes = []
    for repo in args.repo:
        owner, _, name = repo.partition("/")
        if not (owner and name):
            log.error("--repo takes owner/name, for example acme/api")
            return 2
        scopes.append(("repo " + repo, "%s/repos/%s/%s/hooks" % (API, owner, name)))
    for org in args.org:
        scopes.append(("org " + org, "%s/orgs/%s/hooks" % (API, org)))

    unsigned = refusing = total = 0
    for label, base in scopes:
        for hook in page(session, base, per_page=100):
            total += 1
            rejected = delivered = 0
            if args.max_deliveries:
                rejected, delivered = unauthorized(
                    page(session, "%s/%s/deliveries" % (base, hook.get("id")),
                         limit=args.max_deliveries, per_page=100))
            state, detail = verdict(hook, rejected, delivered)
            url = (hook.get("config") or {}).get("url", "?")
            line = "%-8s %s %s  %s" % (state, label, url, detail)
            if state == "signed":
                log.info(line)
                continue
            log.warning(line)
            if state == "unsigned":
                unsigned += 1
                log.warning("  repair: set a high-entropy secret on this hook, "
                            "then make the receiver reject any request without "
                            "X-Hub-Signature-256 rather than skipping the check")
            elif state == "rejected":
                refusing += 1
                log.warning("  repair: compare the secret in your receiver's "
                            "environment with the one on the hook, then replay "
                            "with POST %s/%s/deliveries/{delivery_id}/attempts",
                            base, hook.get("id"))

    log.info("%d hook(s), %d unsigned, %d rejecting deliveries",
             total, unsigned, refusing)
    return 1 if (unsigned or refusing) else 0


if __name__ == "__main__":
    sys.exit(main())
