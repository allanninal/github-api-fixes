from github_duplicate_hooks import endpoint, group, guid_pairs, overlap


def hook(source, url, events, active=True, hid=1):
    return {"source": source, "id": hid, "url": url, "events": events,
            "active": active}


def test_endpoint_ignores_the_ways_two_urls_differ_cosmetically():
    same = "hooks.example.com/gh"
    assert endpoint("https://hooks.example.com/gh") == same
    assert endpoint("https://hooks.example.com/gh/") == same
    assert endpoint("HTTPS://Hooks.Example.com/gh") == same
    assert endpoint("http://hooks.example.com/gh?token=x") == same
    assert endpoint("https://hooks.example.com:8443/gh") == "hooks.example.com:8443/gh"
    assert endpoint(None) == ""


def test_overlap_treats_a_wildcard_as_covering_everything():
    assert overlap(["push"], ["push", "issues"]) == ["push"]
    assert overlap(["*"], ["push", "issues"]) == ["issues", "push"]
    assert overlap(["*"], ["*"]) == ["*"]
    assert overlap(["push"], ["issues"]) == []


def test_one_url_in_two_scopes_with_shared_events_is_the_finding():
    rows = group([hook("org acme", "https://hooks.example.com/gh", ["push"], hid=1),
                  hook("repo acme/api", "https://hooks.example.com/gh/",
                       ["push", "issues"], hid=2)])
    assert len(rows) == 1
    assert rows[0]["state"] == "duplicate"
    assert rows[0]["shared"] == ["push"]


def test_a_deliberate_split_is_not_reported_as_a_duplicate():
    rows = group([hook("org acme", "https://hooks.example.com/gh", ["push"], hid=1),
                  hook("repo acme/api", "https://hooks.example.com/gh",
                       ["issues"], hid=2)])
    assert rows[0]["state"] == "disjoint"
    assert rows[0]["shared"] == []


def test_an_inactive_second_hook_is_latent_rather_than_duplicate():
    rows = group([hook("org acme", "https://hooks.example.com/gh", ["push"], hid=1),
                  hook("repo acme/api", "https://hooks.example.com/gh", ["push"],
                       active=False, hid=2)])
    assert rows[0]["state"] == "latent"


def test_a_single_hook_is_unique():
    rows = group([hook("repo acme/api", "https://hooks.example.com/gh", ["push"])])
    assert rows[0]["state"] == "unique"


def test_guid_pairs_says_whether_delivery_id_dedup_would_help():
    shared = guid_pairs({
        "org acme": [{"guid": "g1", "event": "push",
                      "delivered_at": "2026-08-01T10:00:03Z"}],
        "repo acme/api": [{"guid": "g1", "event": "push",
                           "delivered_at": "2026-08-01T10:00:03Z"}],
    })
    assert shared["shared_guids"] == 1
    assert shared["same_event_different_guid"] == 0

    split = guid_pairs({
        "org acme": [{"guid": "g1", "event": "push",
                      "delivered_at": "2026-08-01T10:00:03Z"}],
        "repo acme/api": [{"guid": "g2", "event": "push",
                           "delivered_at": "2026-08-01T10:00:04Z"}],
    })
    assert split["shared_guids"] == 0
    assert split["same_event_different_guid"] == 1
