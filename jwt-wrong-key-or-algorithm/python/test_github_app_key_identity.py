import base64

from github_app_key_identity import (
    ESCAPED_NEWLINE, inspect_pem, interpret, issuer_form, reconcile, repair_for,
    unwrap, usable,
)

FILLER = base64.b64encode(b"x" * 1200).decode()


def pem(label, body=None):
    """An obviously fake PEM: a real label and a run of filler bytes."""
    chunk = body if body is not None else FILLER
    rows = [chunk[i:i + 64] for i in range(0, len(chunk), 64)]
    return "-----BEGIN %s-----\n%s\n-----END %s-----\n" % (
        label, "\n".join(rows), label)


def test_the_key_github_issues_is_recognised_and_fingerprinted():
    out = inspect_pem(pem("RSA PRIVATE KEY"))
    assert out["state"] == "pkcs1-rsa-key"
    assert out["label"] == "RSA PRIVATE KEY"
    assert len(out["fingerprint"]) == 16
    assert out["der_bytes"] >= 500
    assert usable(out["state"])


def test_a_pkcs8_wrapper_is_the_same_key_and_is_accepted():
    assert inspect_pem(pem("PRIVATE KEY"))["state"] == "pkcs8-key"


def test_the_fingerprint_identifies_the_file_and_nothing_else():
    one = inspect_pem(pem("RSA PRIVATE KEY"))["fingerprint"]
    again = inspect_pem(pem("RSA PRIVATE KEY"))["fingerprint"]
    other = inspect_pem(pem("RSA PRIVATE KEY",
                            base64.b64encode(b"y" * 1200).decode()))["fingerprint"]
    assert one == again
    assert one != other


def test_escaped_newlines_are_the_headline_deployment_fault():
    flattened = pem("RSA PRIVATE KEY").replace("\n", ESCAPED_NEWLINE)
    out = inspect_pem(flattened)
    assert out["state"] == "escaped-newlines"
    assert "backslash and n" in repair_for(out["state"])


def test_a_pem_collapsed_onto_one_line_is_told_apart_from_an_escaped_one():
    collapsed = pem("RSA PRIVATE KEY").replace("\n", " ")
    assert inspect_pem(collapsed)["state"] == "single-line-pem"


def test_the_wrong_kind_of_key_is_named_rather_than_guessed_at():
    assert inspect_pem(pem("OPENSSH PRIVATE KEY"))["state"] == "openssh-format"
    assert inspect_pem(pem("PUBLIC KEY"))["state"] == "public-key-not-private"
    assert inspect_pem(pem("EC PRIVATE KEY"))["state"] == "not-an-rsa-key"
    assert inspect_pem(pem("CERTIFICATE"))["state"] == "certificate-not-key"
    assert inspect_pem(pem("ENCRYPTED PRIVATE KEY"))["state"] == "encrypted-key"
    assert inspect_pem(pem("DH PARAMETERS"))["state"] == "unknown-pem-label"
    for state in ("openssh-format", "public-key-not-private", "not-an-rsa-key"):
        assert not usable(state)


def test_a_truncated_pem_is_caught_before_its_body_is_read():
    cut = pem("RSA PRIVATE KEY").split("-----END")[0]
    assert inspect_pem(cut)["state"] == "truncated-pem"


def test_a_body_that_is_not_base64_says_so():
    assert inspect_pem(pem("RSA PRIVATE KEY", "not base64 at all"))["state"] \
        in ("body-not-base64", "too-small-for-rsa")


def test_something_far_too_small_to_be_an_rsa_key_is_rejected():
    small = base64.b64encode(b"z" * 64).decode()
    out = inspect_pem(pem("RSA PRIVATE KEY", small))
    assert out["state"] == "too-small-for-rsa"
    assert out["fingerprint"] is not None


def test_an_absent_key_is_a_state_and_not_a_crash():
    assert inspect_pem("")["state"] == "no-key-present"
    assert inspect_pem(None)["state"] == "no-key-present"
    assert inspect_pem("just some text")["state"] == "not-a-pem"


def test_a_base64_wrapped_pem_is_unwrapped_rather_than_rejected():
    raw = pem("RSA PRIVATE KEY")
    wrapped = base64.b64encode(raw.encode()).decode()
    text, was_wrapped = unwrap(wrapped)
    assert was_wrapped is True
    assert inspect_pem(text)["state"] == "pkcs1-rsa-key"
    assert unwrap(raw) == (raw.strip(), False)


def test_the_issuer_claim_is_checked_for_shape_only():
    assert issuer_form("123456") == "app-id"
    assert issuer_form("Iv23liABCDEfghij") == "client-id"
    assert issuer_form("acme-deploy-bot") == "unusable-issuer"
    assert issuer_form("") == "no-issuer"


def test_one_decode_message_covers_five_causes_and_says_so():
    state, detail = interpret(401, "A JSON web token could not be decoded")
    assert state == "signature-rejected"
    assert "another App" in detail
    assert "RS256" in detail


def test_the_neighbouring_failures_are_handed_off_rather_than_absorbed():
    assert interpret(200, None)[0] == "key-accepted"
    assert interpret(404, "Integration not found")[0] == "issuer-does-not-resolve"
    assert interpret(401, "'Issued at' claim ('iat') is in the "
                          "future")[0] == "clock-problem-not-key"
    assert interpret(401, "'Expiration time' claim ('exp') is too far in the "
                          "future")[0] == "lifetime-problem-not-key"
    assert interpret(401, "Bad credentials")[0] == "not-a-jwt"
    assert interpret(403, "Resource not accessible by integration")[0] == "unrelated"


def test_a_working_key_for_the_wrong_app_is_the_finding_with_no_error():
    app = {"id": 654321, "client_id": "Iv23liZZZZ", "slug": "acme-staging-bot",
           "name": "Acme Staging Bot"}
    state, detail = reconcile(app, "acme-deploy-bot")
    assert state == "authenticated-as-another-app"
    assert "staging key reaches production" in detail
    assert reconcile(app, "acme-staging-bot")[0] == "identity-matches"
    assert reconcile(app, "654321")[0] == "identity-matches"
    assert reconcile(app, "Iv23liZZZZ")[0] == "identity-matches"
    assert reconcile(app, None)[0] == "no-expectation-given"
    assert reconcile(None, "acme")[0] == "no-app-body"
