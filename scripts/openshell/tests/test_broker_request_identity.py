from __future__ import annotations

import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from scripts.openshell import broker_request_identity as module

KEY = bytes(range(module.KEY_BYTES))
COMMON = {
    "profile": "siq_analysis",
    "run_id": "run-123",
    "sandbox_id": "siq-analysis-run-123",
    "session_id": "run-123",
    "policy_digest": "a" * 64,
    "run_nonce_digest": "b" * 64,
}


def test_signed_identity_round_trips_without_request_content() -> None:
    token = module.sign_identity(KEY, now=1_000, ttl_seconds=60, **COMMON)

    identity = module.verify_identity(token, KEY, now=1_010)

    assert identity.profile == "siq_analysis"
    assert identity.run_id == "run-123"
    assert identity.sandbox_id == "siq-analysis-run-123"
    assert identity.session_id == "run-123"
    assert identity.policy_digest == "a" * 64
    assert "prompt" not in token.lower()
    assert "secret" not in token.lower()


def test_reusable_bundle_issues_audience_limited_tokens_with_one_lifetime() -> None:
    bundle = module.issue_broker_identities(KEY, now=1_000, ttl_seconds=60, **COMMON)

    egress = module.verify_identity(bundle.egress_token, KEY, now=1_010)
    data = module.verify_identity(bundle.data_token, KEY, now=1_010)
    assert egress.audience == module.EGRESS_AUDIENCE
    assert data.audience == module.DATA_AUDIENCE
    assert egress.issued_at == data.issued_at == bundle.issued_at == 1_000
    assert egress.expires_at == data.expires_at == bundle.expires_at == 1_060
    assert bundle.as_environment() == {
        module.EGRESS_TOKEN_ENV: bundle.egress_token,
        module.DATA_TOKEN_ENV: bundle.data_token,
    }
    assert bundle.secret_values() == (bundle.egress_token, bundle.data_token)
    assert bundle.egress_token not in repr(bundle)
    assert bundle.data_token not in repr(bundle)


def test_signature_claim_and_gateway_tampering_are_rejected() -> None:
    token = module.sign_identity(KEY, now=1_000, ttl_seconds=60, **COMMON)
    encoded = token.split(".")
    payload = module._b64decode(encoded[1]).decode("ascii").replace("run-123", "run-other")
    encoded[1] = module._b64encode(payload.encode("ascii"))
    tampered = ".".join(encoded)

    with pytest.raises(module.IdentityError, match="signature_invalid"):
        module.verify_identity(tampered, KEY, now=1_010)
    with pytest.raises(module.IdentityError, match="signature_invalid"):
        module.verify_identity(token, b"x" * module.KEY_BYTES, now=1_010)


def test_expired_future_and_wrong_gateway_tokens_fail_closed() -> None:
    token = module.sign_identity(KEY, now=1_000, ttl_seconds=60, **COMMON)
    with pytest.raises(module.IdentityError, match="expired"):
        module.verify_identity(token, KEY, now=1_121)
    with pytest.raises(module.IdentityError, match="expired"):
        module.verify_identity(token, KEY, now=900)

    with pytest.raises(module.IdentityError, match="gateway_mismatch"):
        module.verify_identity(token, KEY, expected_gateway="other-gateway", now=1_010)

    with pytest.raises(module.IdentityError, match="time_invalid"):
        module.verify_identity(token, KEY, now=True)
    with pytest.raises(module.IdentityError, match="ttl_invalid"):
        module.sign_identity(KEY, now=1_000, ttl_seconds=True, **COMMON)


def test_key_file_is_private_and_rejects_wrong_owner_mode_or_link(tmp_path: Path) -> None:
    path = tmp_path / "identity.key"
    value = module.ensure_key_file(path)
    assert len(value) == module.KEY_BYTES
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert module.read_key_file(path) == value

    path.chmod(0o640)
    with pytest.raises(module.IdentityError, match="key_file_invalid"):
        module.read_key_file(path)
    path.chmod(0o600)
    hardlink = tmp_path / "identity-hardlink.key"
    hardlink.hardlink_to(path)
    with pytest.raises(module.IdentityError, match="key_file_invalid"):
        module.read_key_file(hardlink)


def test_key_file_creation_does_not_follow_symlink(tmp_path: Path) -> None:
    target = tmp_path / "outside"
    target.write_text("unchanged\n", encoding="ascii")
    path = tmp_path / "identity.key"
    path.symlink_to(target)
    with pytest.raises(module.IdentityError, match="key_file_invalid"):
        module.ensure_key_file(path)
    assert target.read_text(encoding="ascii") == "unchanged\n"


def test_concurrent_key_creation_never_replaces_the_winner(tmp_path: Path) -> None:
    path = tmp_path / "identity.key"

    with ThreadPoolExecutor(max_workers=8) as executor:
        values = list(executor.map(lambda _index: module.ensure_key_file(path), range(32)))

    assert len(set(values)) == 1
    assert module.read_key_file(path) == values[0]
    assert not list(tmp_path.glob(".identity.key.*.tmp"))


def test_key_parent_must_be_private_owned_directory(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o755)
    unsafe.chmod(0o755)

    with pytest.raises(module.IdentityError, match="key_parent_invalid"):
        module.ensure_key_file(unsafe / "identity.key")


def test_noncanonical_base64_token_is_rejected() -> None:
    token = module.sign_identity(KEY, now=1_000, ttl_seconds=60, **COMMON)
    version, payload, signature = token.split(".")

    with pytest.raises(module.IdentityError, match="token_invalid"):
        module.verify_identity(f"{version}.{payload}.{signature}=", KEY, now=1_010)


def test_header_verification_requires_exactly_one_matching_profile() -> None:
    token = module.sign_identity(KEY, now=1_000, ttl_seconds=60, **COMMON)

    identity = module.verify_header_values([token], KEY, now=1_010)
    assert identity.run_id == COMMON["run_id"]

    with pytest.raises(module.IdentityError, match="header_required"):
        module.verify_header_values([], KEY, now=1_010)
    with pytest.raises(module.IdentityError, match="header_invalid"):
        module.verify_header_values([token, token], KEY, now=1_010)
    with pytest.raises(module.IdentityError, match="profile_mismatch"):
        module.verify_header_values([token], KEY, expected_profile="siq_factchecker", now=1_010)
    with pytest.raises(module.IdentityError, match="audience_mismatch"):
        module.verify_header_values(
            [token],
            KEY,
            expected_audience="siq-read-only-data-broker",
            now=1_010,
        )


def test_key_rotation_replaces_private_key_and_rejects_old_tokens(tmp_path: Path) -> None:
    path = tmp_path / "identity.key"
    before = module.ensure_key_file(path)
    token = module.sign_identity(before, now=1_000, ttl_seconds=60, **COMMON)

    after = module.rotate_key_file(path)

    assert after != before
    assert module.read_key_file(path) == after
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(module.IdentityError, match="signature_invalid"):
        module.verify_identity(token, after, now=1_010)


def test_request_identity_context_is_scoped_and_restored() -> None:
    identity = module.verify_identity(
        module.sign_identity(KEY, now=1_000, ttl_seconds=60, **COMMON),
        KEY,
        now=1_010,
    )
    assert module.current_request_identity() is None

    with module.request_identity_context(identity):
        assert module.current_request_identity() == identity

    assert module.current_request_identity() is None
