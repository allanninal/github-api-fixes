from github_route_or_verb import (
    DOCS_INDEX, ROUTE_TABLE, SAFE_VERBS, classify_not_found, docs_url_kind,
    documentation_url_of, get_probe_is_evidence, match_route,
    path_shape_problem, permissions_header_hint, probe_refusal, read_cost,
    repair, root_map_covers, verb_verdict, verdict,
)

# The two shapes GitHub really returns. The difference between them is the
# whole note, so they are held verbatim rather than described.
ROUTED_404 = {"message": "Not Found",
              "documentation_url": "https://docs.github.com/rest/repos/repos#get-a-repository",
              "status": "404"}
UNROUTED_404 = {"message": "Not Found",
                "documentation_url": "https://docs.github.com/rest",
                "status": "404"}

ROOT_MAP = {"current_user_url": "https://api.github.com/user",
            "repository_url": "https://api.github.com/repos/{owner}/{repo}",
            "emojis_url": "https://api.github.com/emojis"}


def test_the_documentation_url_is_the_discriminator():
    assert docs_url_kind(documentation_url_of(ROUTED_404))[0] == "endpoint-specific"
    assert docs_url_kind(documentation_url_of(UNROUTED_404))[0] == "generic"
    assert docs_url_kind(DOCS_INDEX + "/")[0] == "generic"
    assert docs_url_kind(None)[0] == "absent"
    assert docs_url_kind("https://example.invalid/docs")[0] == "unrecognised"


def test_a_routed_404_is_somebody_elses_note():
    state, detail = classify_not_found(404, ROUTED_404)
    assert state == "route-matched-resource-missing"
    assert "different note" in detail
    assert verdict(state, "clean", "verb-not-on-this-route")[0] == "resource-not-routing"


def test_an_unrouted_404_keeps_the_investigation_here():
    assert classify_not_found(404, UNROUTED_404)[0] == "nothing-routed-here"
    assert classify_not_found(200, None)[0] == "route-answers-get"
    assert classify_not_found(401, {})[0] == "unauthenticated"
    assert classify_not_found(403, {})[0] == "refused-not-missing"
    assert classify_not_found(502, {})[0] == "unexpected-status"


def test_the_trailing_slash_that_is_invisible_in_review():
    state, detail = path_shape_problem("/repos/acme/payments/")
    assert state == "trailing-slash"
    assert "documents it as a cause of 404" in detail
    assert path_shape_problem("/repos/acme/payments")[0] == "clean"
    assert path_shape_problem("/repos/acme/payments?per_page=1")[0] == "clean"


def test_the_other_documented_shape_errors():
    assert path_shape_problem("/repos/{owner}/payments")[0] == "placeholder-not-substituted"
    assert path_shape_problem("/repos//payments")[0] == "doubled-slash"
    assert path_shape_problem("/repos/acme/my payments")[0] == "unencoded-space"
    assert path_shape_problem("https://api.github.com/user")[0] == "full-url-not-path"
    assert path_shape_problem("repos/acme/payments")[0] == "no-leading-slash"
    assert path_shape_problem("")[0] == "empty-path"


def test_the_matcher_is_segment_wise_so_a_smuggled_slash_does_not_match():
    template, verbs, _ = match_route("/repos/acme/payments/collaborators/dana")
    assert template == "/repos/{owner}/{repo}/collaborators/{username}"
    assert set(verbs) == {"get", "put", "delete"}
    # A branch name containing a slash adds a segment, so it is a different
    # route, which is exactly what GitHub thinks too.
    assert match_route("/repos/acme/payments/branches/release/1.0/protection")[0] is None
    assert match_route("/repos/acme/payments/nothing-like-this")[0] is None


def test_the_wrong_verb_is_named_with_the_documented_one():
    state, detail = verb_verdict("/repos/acme/payments/collaborators/dana", "post")
    assert state == "verb-not-on-this-route"
    assert "you sent POST" in detail
    assert "PUT" in detail
    assert verb_verdict("/repos/acme/payments/topics", "put")[0] == "verb-is-documented"
    assert verb_verdict("/some/unknown/path", "put")[0] == "route-not-in-table"


def test_a_get_probe_cannot_prove_a_route_with_no_get():
    state, detail = get_probe_is_evidence("/repos/acme/payments/merges")
    assert state == "probe-cannot-decide"
    assert "proves nothing" in detail
    assert get_probe_is_evidence("/repos/acme/payments/topics")[0] == "probe-decides"
    assert get_probe_is_evidence("/nope")[0] == "unknown-route"


def test_the_script_refuses_to_probe_with_a_write_and_says_both_reasons():
    state, detail = probe_refusal("put")
    assert state == "will-not-probe"
    assert "would be a write" in detail
    assert "returns no information" in detail
    assert probe_refusal("get")[0] == "safe-to-send"
    assert probe_refusal("head")[0] == "safe-to-send"
    # And no table entry claims a changing verb is safe to send.
    assert set(SAFE_VERBS) == {"get", "head"}


def test_no_route_in_the_table_is_missing_its_note():
    for template, verbs, note in ROUTE_TABLE:
        assert template.startswith("/"), template
        assert verbs, template
        assert note, template
        assert all(v == v.lower() for v in verbs), template


def test_the_verdict_puts_path_shape_before_the_verb():
    # A malformed path with a wrong verb is a path problem first: fixing the
    # verb on a path that cannot match anything changes nothing.
    state, _ = verdict("nothing-routed-here", "trailing-slash", "verb-not-on-this-route")
    assert state == "path-shape-wrong"
    assert verdict("nothing-routed-here", "clean", "verb-not-on-this-route")[0] == "wrong-verb"
    assert verdict("route-answers-get", "clean", "verb-is-documented")[0] == (
        "route-and-verb-both-fine")
    assert verdict("nothing-routed-here", "clean", "verb-is-documented")[0] == (
        "route-absent-or-wrong-host")


def test_the_permission_header_is_corroboration_and_says_so():
    state, detail = permissions_header_hint({"X-Accepted-GitHub-Permissions": "issues=read"})
    assert state == "permissions-were-evaluated"
    assert "Corroboration only" in detail
    assert permissions_header_hint({})[0] == "no-permission-header"
    assert "too weak" in permissions_header_hint({})[1]


def test_the_root_map_is_a_hint_and_admits_its_coverage():
    assert root_map_covers(ROOT_MAP, "/repos/acme/payments")[0] == "family-known"
    state, detail = root_map_covers(ROOT_MAP, "/packages/npm/thing")
    assert state == "family-not-in-map"
    assert "hint and not a finding" in detail
    assert root_map_covers({}, "/repos/a/b")[0] == "root-unread"


def test_the_repair_names_the_verb_and_does_not_send_it():
    fix = repair("wrong-verb", "/repos/acme/payments/collaborators/dana", "post")
    assert "send PUT or DELETE" in fix
    assert "Nothing here sends it" in fix
    assert "wrong GitHub installation" in repair("route-absent-or-wrong-host", "/x", "get")
    assert "Do not send the verb" in repair("undetermined", "/x", "put")


def test_the_read_cost_is_known_before_anything_is_spent():
    assert read_cost(False) == 1
    assert read_cost(True) == 2
