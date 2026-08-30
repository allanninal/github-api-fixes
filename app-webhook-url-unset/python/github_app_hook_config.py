"""Say whether a GitHub App has a webhook destination that can work.

Read only. Three GETs against the App itself: its own record, its webhook
configuration, and a page of its deliveries. Nothing is created or changed.

A GitHub App's webhook lives on the App rather than on each installation, and it
is independent of the App's event subscriptions. It can be blank, or left
pointing at the smee.io proxy the quickstart hands out, and nothing complains:
there are no failed deliveries where there are no deliveries at all.

Authentication is a JWT signed with the App's private key. This script takes the
JWT from the environment and never loads or signs with the key, so the key never
enters this process.

Environment:

    GITHUB_APP_JWT   a JWT signed with the App's private key
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_app_hook_config")

API = "https://api.github.com"
UA = "github-app-hook-config/1.0"

# The four ways this actually happens. All of them pass a check that only asks
# whether the field is empty, which is why the host is classified instead.
PLACEHOLDER_HOSTS = ("example.com", "example.org", "example.net",
                     "your-domain.com", "yourdomain.com", "changeme", "todo")
TUNNEL_HOSTS = ("smee.io", "ngrok.io", "ngrok-free.app", "ngrok.app",
                "loca.lt", "trycloudflare.com", "serveo.net")
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1")
# Past this, a destination that has delivered before has gone quiet.
DEFAULT_STALE_DAYS = 30


def host_of(url):
    """The lowercase hostname of a URL, or an empty string. Pure."""
    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    return (parts.hostname or "").lower()


def host_matches(host, suffixes):
    """Whether a host is one of these names or a subdomain of one. Pure."""
    for suffix in suffixes:
        if host == suffix or host.endswith("." + suffix):
            return True
    return False


def url_class(url):
    """Sort a webhook destination into what it can actually reach. Pure.

    Ordered so the most specific reason wins: http://localhost is a loopback
    problem, not a transport one, and sending the reader to the transport note
    would waste their afternoon.
    """
    raw = str(url or "").strip()
    if not raw:
        return "unset"
    parts = urlsplit(raw)
    host = host_of(raw)
    if parts.scheme not in ("http", "https") or not host:
        return "malformed"
    if host_matches(host, PLACEHOLDER_HOSTS) or host.startswith("example."):
        return "placeholder"
    if host_matches(host, TUNNEL_HOSTS):
        return "tunnel"
    if host in LOOPBACK_HOSTS or host.endswith(".local"):
        return "loopback"
    if parts.scheme == "http":
        return "insecure"
    return "production"


def secret_state(config):
    """Whether a secret is set. Never returns the value. Pure."""
    if not isinstance(config, dict):
        return "unknown"
    return "set" if config.get("secret") is not None else "absent"


def content_type_of(config):
    """The App hook's body encoding, with the documented default applied. Pure."""
    if not isinstance(config, dict):
        return "unknown"
    raw = config.get("content_type")
    if raw is None:
        return "form"
    value = str(raw).strip().lower()
    return value if value in ("json", "form") else "unknown"


def subscribed_events(app):
    """The events the App is subscribed to. Pure."""
    events = (app or {}).get("events") if isinstance(app, dict) else None
    return [str(e) for e in events] if isinstance(events, list) else []


def parse_time(text):
    """An ISO 8601 timestamp as an aware datetime, or None. Pure."""
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def last_delivery(deliveries):
    """The most recent delivered_at in a delivery list, or None. Pure."""
    stamps = []
    for record in deliveries or []:
        if not isinstance(record, dict):
            continue
        moment = parse_time(record.get("delivered_at"))
        if moment is not None:
            stamps.append(moment)
    return max(stamps) if stamps else None


def delivery_state(deliveries, now, stale_days=DEFAULT_STALE_DAYS):
    """Whether anything has arrived recently. Corroboration, never proof. Pure.

    Deliveries are retained for a limited window, so an empty list can also mean
    a quiet App. This is only worth reading next to the subscription list.
    """
    if deliveries is None:
        return "unknown"
    if not deliveries:
        return "none"
    latest = last_delivery(deliveries)
    if latest is None or now is None:
        return "unknown"
    days = int((now - latest).total_seconds() // 86400)
    return "stale" if days >= int(stale_days) else "recent"


def verdict(url, events, deliveries_state, installations=None):
    """Turn the destination, the subscriptions and the log into a finding. Pure."""
    klass = url_class(url)
    count = len(events or [])
    if klass == "unset":
        if count:
            return ("no-url-subscribed",
                    "the App subscribes to %d event(s) and has no webhook URL, "
                    "so nothing is delivered and nothing fails. There is no log "
                    "to read because there are no deliveries." % count)
        return ("no-url",
                "the App has no webhook URL and subscribes to no events. That "
                "is a coherent configuration for an App that only polls or "
                "creates its own repository hooks, so this is reported rather "
                "than judged.")
    if klass == "malformed":
        return ("malformed-url",
                "the webhook URL is not a usable http or https URL, so no "
                "delivery can be attempted against it.")
    if klass == "placeholder":
        return ("placeholder-url",
                "the webhook URL points at a placeholder host from a template. "
                "It looks configured and it reaches nothing you own.")
    if klass == "tunnel":
        return ("tunnel-url",
                "the App delivers to a development proxy from the quickstart. "
                "Every event goes to a channel nobody is listening to, and the "
                "field looks filled in to anyone glancing at it.")
    if klass == "loopback":
        return ("loopback-url",
                "the webhook URL is a loopback or link-local address, which "
                "GitHub cannot reach from the internet at all.")
    if klass == "insecure":
        return ("insecure-url",
                "the App delivers over plain http, so payloads and signatures "
                "cross the network in the clear. Deliveries do arrive, which is "
                "why this survives so long.")
    if deliveries_state == "none" and count:
        return ("no-deliveries",
                "the URL looks like a real destination and the App subscribes "
                "to %d event(s), but nothing has been delivered in the retained "
                "window. Either the events have genuinely not happened or the "
                "destination has never worked." % count)
    if deliveries_state == "stale" and count:
        return ("silent",
                "the destination has delivered before and has gone quiet. That "
                "is a receiver or subscription question rather than a "
                "configuration one.")
    if not count:
        return ("no-events",
                "the webhook URL is a real destination but the App subscribes "
                "to no events, so nothing will ever be sent to it. That is a "
                "subscription finding, not a URL one.")
    return ("delivering",
            "the App has a real destination, subscribes to %d event(s), and "
            "events are arriving." % count)


def repair(state):
    """The sentence a reader has to act on. Pure."""
    if state in ("no-url-subscribed", "placeholder-url", "tunnel-url",
                 "loopback-url", "malformed-url"):
        return ("point the App's webhook at the production receiver, set a "
                "secret, set content_type to json, and then confirm with GET "
                "/app/hook/deliveries that events start arriving. The settings "
                "page will show a URL that nothing can reach.")
    if state == "insecure-url":
        return ("move the destination to https before anything else. The "
                "payload and its signature are readable in transit today.")
    if state == "no-deliveries":
        return ("check the receiver is reachable from the internet, then wait "
                "for an event you can cause on purpose and read the delivery "
                "log again. An empty log alone is not proof of anything.")
    if state == "no-events":
        return ("subscribe the App to the events it handles. The destination is "
                "fine and there is nothing being sent to it.")
    if state == "no-url":
        return ("nothing, if the App is meant to poll or manage its own "
                "repository hooks. If it is meant to react to events, this is "
                "the whole problem.")
    if state == "silent":
        return ("look at the receiver and the subscription list rather than the "
                "URL, which is working.")
    return "nothing."


def get(session, path):
    """One GET. Returns (status, json-or-None)."""
    r = session.get(API + path, timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS,
                    help="days without a delivery before a destination is quiet")
    args = ap.parse_args()

    jwt = os.environ.get("GITHUB_APP_JWT")
    if not jwt:
        log.error("set GITHUB_APP_JWT to a JWT signed with the App's private "
                  "key. An installation token cannot read the App's own "
                  "configuration and will answer 403 here")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + jwt,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    status, app = get(session, "/app")
    if status != 200 or not isinstance(app, dict):
        log.error("GET /app returned %d; the JWT is not being accepted as an App", status)
        return 2
    events = subscribed_events(app)
    log.info("app: %s, %s installation(s), subscribed to %d event(s)",
             app.get("slug"), app.get("installations_count"), len(events))

    status, config = get(session, "/app/hook/config")
    if status != 200 or not isinstance(config, dict):
        log.error("GET /app/hook/config returned %d", status)
        return 2
    url = config.get("url")
    log.info("hook config: url=%s content_type=%s secret=%s",
             url or "(empty)", content_type_of(config), secret_state(config))

    status, deliveries = get(session, "/app/hook/deliveries?per_page=100")
    records = deliveries if status == 200 and isinstance(deliveries, list) else None
    now = datetime.now(timezone.utc)
    state_of_log = delivery_state(records, now, args.stale_days)
    latest = last_delivery(records or [])
    log.info("deliveries: %s in the retained window, most recent %s",
             len(records) if records is not None else "unreadable",
             str(latest)[:10] if latest else "none")

    state, detail = verdict(url, events, state_of_log, app.get("installations_count"))
    log.info("%s: %s", state, detail)
    log.info("repair: %s", repair(state))

    print(json.dumps({
        "app": app.get("slug"),
        "installations": app.get("installations_count"),
        "events": events,
        "hook_url": url,
        "url_class": url_class(url),
        "content_type": content_type_of(config),
        "secret": secret_state(config),
        "deliveries_retained": len(records) if records is not None else None,
        "last_delivery": str(latest) if latest else None,
        "delivery_state": state_of_log,
        "state": state,
        "detail": detail,
        "repair": repair(state),
    }, indent=2, default=str))
    return 1 if state in ("no-url-subscribed", "placeholder-url", "tunnel-url",
                          "loopback-url", "malformed-url", "insecure-url") else 0


if __name__ == "__main__":
    sys.exit(main())
