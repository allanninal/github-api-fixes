"""Say whether a webhook sends a body encoding its receiver cannot read.

Read only. Three kinds of GET: the hook list, for config.content_type; the
delivery list, for recent attempts; and a few individual delivery records, which
are the only place the request headers and the recorded body appear. Nothing is
created, edited or redelivered.

config.content_type defaults to form, which wraps the event JSON inside a
urlencoded payload= field. A receiver written for application/json either
rejects that body or, much more expensively, accepts it and parses nothing while
answering 200.

The API cannot see what your receiver parses. That half is declared with
--receiver and the output says which half was measured and which was told.

Environment:

    GITHUB_TOKEN   a read-only token with access to the repository
"""
import argparse
import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("github_hook_content_type")

API = "https://api.github.com"
UA = "github-hook-content-type/1.0"

FORM = "form"
JSON = "json"
# The documented default. A hook created without naming a content type is a form
# hook, and config comes back with the key absent rather than set.
DEFAULT_CONTENT_TYPE = FORM
# The statuses a strict parser answers a body it cannot read with. Corroboration
# only: the tolerant frameworks answer 200 and this count stays at zero.
PARSE_STATUSES = (400, 415, 422)


def content_type_of(config):
    """The hook's configured body encoding, normalised. Pure.

    An absent key is not unknown, it is form. Reporting it as unknown would lose
    the most common way a hook ends up form-encoded, which is nobody choosing.
    """
    if not isinstance(config, dict):
        return "unknown"
    raw = config.get("content_type")
    if raw is None:
        return DEFAULT_CONTENT_TYPE
    value = str(raw).strip().lower()
    if value in ("json", "application/json"):
        return JSON
    if value in ("form", "application/x-www-form-urlencoded"):
        return FORM
    return "unknown"


def content_type_was_explicit(config):
    """Whether the hook names its encoding or inherits the default. Pure."""
    return isinstance(config, dict) and config.get("content_type") is not None


def header_of(headers, name):
    """One header from a delivery record, case-insensitively. Pure."""
    if not isinstance(headers, dict):
        return None
    wanted = str(name).strip().lower()
    for key, value in headers.items():
        if str(key).strip().lower() == wanted:
            return value
    return None


def encoding_of_header(value):
    """Classify a content-type header value, ignoring parameters. Pure."""
    if value is None:
        return "unknown"
    text = str(value).split(";")[0].strip().lower()
    if text == "application/json":
        return JSON
    if text == "application/x-www-form-urlencoded":
        return FORM
    return "unknown"


def delivery_encoding(delivery):
    """What GitHub said it was sending on one delivery record. Pure."""
    if not isinstance(delivery, dict):
        return "unknown"
    request = delivery.get("request")
    if not isinstance(request, dict):
        return "unknown"
    return encoding_of_header(header_of(request.get("headers"), "content-type"))


def is_form_wrapped(payload):
    """Whether a recorded body is the payload= wrapper rather than the event. Pure.

    A form delivery records one key, payload, holding the event JSON as a string.
    An event object has many keys and none of that shape, so this is unambiguous
    where it fires and simply silent where the record has been normalised.
    """
    if not isinstance(payload, dict):
        return False
    return list(payload.keys()) == ["payload"] and isinstance(payload.get("payload"), str)


def wrapper_evidence(details):
    """Count the delivery records showing the form wrapper, both ways. Pure."""
    records = [d for d in (details or []) if isinstance(d, dict)]
    by_header = sum(1 for d in records if delivery_encoding(d) == FORM)
    by_body = sum(1 for d in records
                  if is_form_wrapped((d.get("request") or {}).get("payload")))
    return {"sampled": len(records), "form_header": by_header, "form_wrapper": by_body}


def parse_failures(deliveries):
    """How many recent attempts came back with a body-parse status. Pure."""
    records = [d for d in (deliveries or []) if isinstance(d, dict)]
    hits = 0
    for d in records:
        try:
            code = int(d.get("status_code"))
        except (TypeError, ValueError):
            continue
        if code in PARSE_STATUSES:
            hits += 1
    return hits, len(records)


def receiver_of(declared):
    """Normalise what the caller says the receiver parses. Pure."""
    value = str(declared or "unknown").strip().lower()
    return value if value in (JSON, FORM) else "unknown"


def verdict(hook_encoding, declared, evidence=None, failures=0, sampled_total=0):
    """Turn the configured encoding and the declared receiver into a finding. Pure.

    The status codes never decide. A clean delivery log is the expected state of
    this problem on a tolerant framework, so letting it soften the verdict would
    remove the only case worth writing a script for.
    """
    seen = evidence or {}
    confirmed = max(int(seen.get("form_header") or 0), int(seen.get("form_wrapper") or 0))
    parsed = receiver_of(declared)
    corroboration = ""
    if confirmed:
        corroboration = (" %d of %d sampled deliveries carry the form encoding."
                         % (confirmed, seen.get("sampled") or 0))
    if failures:
        corroboration += (" %d of %d recent attempts came back 400, 415 or 422."
                          % (failures, sampled_total))
    if hook_encoding == "unknown":
        return ("encoding-unknown",
                "config.content_type holds a value this script does not "
                "recognise. GitHub supports json and form; anything else needs "
                "reading by hand before the rest of this is meaningful.")
    if hook_encoding == FORM and parsed == JSON:
        return ("form-to-json",
                "the hook sends application/x-www-form-urlencoded and the "
                "receiver was declared as JSON. Every event arrives wrapped in "
                "a payload= field, so no key your handler reads exists at the "
                "top level of the body." + corroboration)
    if hook_encoding == JSON and parsed == FORM:
        return ("json-to-form",
                "the hook sends application/json and the receiver was declared "
                "as a form parser. The body has no payload= field to unwrap, so "
                "the parsed result is empty rather than wrong.")
    if hook_encoding == FORM and parsed == "unknown":
        return ("receiver-undeclared",
                "the hook sends application/x-www-form-urlencoded, which is the "
                "default rather than a decision. No receiver was declared, so "
                "this is a risk rather than a finding: confirm the handler "
                "unwraps the payload field before treating it as healthy."
                + corroboration)
    if hook_encoding == FORM:
        return ("consistent-form",
                "the hook sends application/x-www-form-urlencoded and the "
                "receiver was declared as a form parser. Consistent, but the "
                "signature covers the urlencoded wrapper, so verify over the raw "
                "bytes rather than over anything you unwrapped.")
    return ("consistent-json",
            "the hook sends application/json and the receiver parses JSON.")


def repair(state):
    """The sentence a reader has to act on. Pure."""
    if state == "form-to-json":
        return ("set config.content_type to json on the hook, and in the same "
                "change make signature verification read the raw request bytes "
                "before parsing. Then redeliver one event from the delivery log "
                "and confirm the handler ran.")
    if state == "json-to-form":
        return ("parse the body as JSON in the receiver. Changing the hook back "
                "to form to suit the parser is the wrong direction: form is the "
                "legacy encoding and it makes signature verification harder.")
    if state == "receiver-undeclared":
        return ("run this again with --receiver set from the handler code. If "
                "the handler reads the body as JSON, this is a live finding; if "
                "it unwraps the payload field first, it is working as built.")
    if state == "consistent-form":
        return ("nothing urgent. Moving to json is still worth doing, because "
                "it removes a layer of encoding between the signature and the "
                "document you verify.")
    if state == "encoding-unknown":
        return ("read config.content_type by hand. Only json and form are "
                "supported values and neither of them is what is set here.")
    return "nothing."


def get(session, path):
    """One GET. Returns (status, json-or-None)."""
    url = API + path if path.startswith("/") else path
    r = session.get(url, timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body


def hooks_for(session, repo):
    """Every hook on the repository."""
    status, body = get(session, "/repos/%s/hooks?per_page=100" % repo)
    if status != 200 or not isinstance(body, list):
        log.error("GET /repos/%s/hooks returned %d", repo, status)
        return []
    return body


def deliveries_for(session, repo, hook_id):
    """The recent delivery list. No request headers here; that needs the detail."""
    status, body = get(session, "/repos/%s/hooks/%s/deliveries?per_page=100"
                       % (repo, hook_id))
    if status != 200 or not isinstance(body, list):
        log.info("deliveries for hook %s returned %d; the config read stands on "
                 "its own", hook_id, status)
        return []
    return body


def delivery_details(session, repo, hook_id, deliveries, sample):
    """Individual records, which are the only place request.headers appears."""
    out = []
    for d in deliveries[:max(0, int(sample))]:
        if not isinstance(d, dict) or d.get("id") is None:
            continue
        status, body = get(session, "/repos/%s/hooks/%s/deliveries/%s"
                           % (repo, hook_id, d["id"]))
        if status == 200 and isinstance(body, dict):
            out.append(body)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPO"),
                    help="owner/name of the repository holding the hook")
    ap.add_argument("--receiver", default=os.environ.get("GITHUB_RECEIVER_PARSES"),
                    help="what your receiver parses: json or form. The API "
                         "cannot see this, so it is declared rather than read")
    ap.add_argument("--sample", type=int, default=5,
                    help="how many individual delivery records to fetch")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("set GITHUB_TOKEN to a read-only token with access to the repository")
        return 2
    if not args.repo:
        log.error("set --repo to owner/name")
        return 2

    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    })

    findings = []
    report = []
    for hook in hooks_for(session, args.repo):
        config = hook.get("config") or {}
        encoding = content_type_of(config)
        explicit = content_type_was_explicit(config)
        log.info("hook %s %s content_type=%s (%s)", hook.get("id"),
                 config.get("url"), encoding,
                 "explicit" if explicit else "default, key absent")

        deliveries = deliveries_for(session, args.repo, hook.get("id"))
        details = delivery_details(session, args.repo, hook.get("id"),
                                   deliveries, args.sample)
        evidence = wrapper_evidence(details)
        failures, total = parse_failures(deliveries)
        log.info("deliveries sampled: %d, form content-type header on %d, "
                 "payload= wrapper on %d", evidence["sampled"],
                 evidence["form_header"], evidence["form_wrapper"])
        log.info("parse statuses (400/415/422): %d of %d recent deliveries",
                 failures, total)

        state, detail = verdict(encoding, args.receiver, evidence, failures, total)
        log.info("%s: %s", state, detail)
        log.info("repair: %s", repair(state))
        if state in ("form-to-json", "json-to-form", "encoding-unknown"):
            findings.append(hook.get("id"))
        report.append({
            "hook_id": hook.get("id"),
            "url": config.get("url"),
            "content_type": encoding,
            "content_type_explicit": explicit,
            "receiver_declared": receiver_of(args.receiver),
            "sampled": evidence["sampled"],
            "form_header_seen": evidence["form_header"],
            "form_wrapper_seen": evidence["form_wrapper"],
            "parse_status_count": failures,
            "deliveries_examined": total,
            "state": state,
            "detail": detail,
            "repair": repair(state),
        })

    print(json.dumps({"repository": args.repo, "hooks": report}, indent=2, default=str))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
