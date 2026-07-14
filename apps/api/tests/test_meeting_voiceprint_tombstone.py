from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest
from services.meeting_voiceprint_tombstone import (
    EMPTY_TOMBSTONE_HEAD_HMAC,
    VoiceprintTombstoneConfigurationError,
    VoiceprintTombstoneIntegrityError,
    VoiceprintTombstoneLedger,
)

PROFILE_ID = "11111111-1111-4111-8111-111111111111"


def _ledger(tmp_path) -> VoiceprintTombstoneLedger:
    return VoiceprintTombstoneLedger(
        path=tmp_path / "runtime" / "security" / "voiceprint-tombstones.jsonl",
        hmac_key=b"t" * 32,
        backend_data_root=tmp_path / "data" / "backend",
    )


def test_append_is_authenticated_idempotent_and_upgrades_revoke_to_delete(tmp_path):
    ledger = _ledger(tmp_path)
    assert ledger.initialize() == 0
    empty_entries, empty_checkpoint = ledger.load_with_checkpoint()
    assert empty_entries == ()
    assert empty_checkpoint.entry_count == 0
    assert empty_checkpoint.head_hmac == EMPTY_TOMBSTONE_HEAD_HMAC
    assert ledger.path.is_file()
    assert ledger.path.stat().st_mode & 0o777 == 0o600
    timestamp = datetime(2026, 7, 13, 10, 30, tzinfo=timezone.utc)
    revoked = ledger.append(
        owner_user_id=7,
        profile_id=PROFILE_ID,
        deleted_at=timestamp,
        reason="revoked",
    )
    replay = ledger.append(
        owner_user_id=7,
        profile_id=PROFILE_ID,
        deleted_at=timestamp,
        reason="revoked",
    )
    deleted = ledger.append(
        owner_user_id=7,
        profile_id=PROFILE_ID,
        deleted_at=timestamp,
        reason="deleted",
    )

    assert replay == revoked
    assert deleted.sequence == 2
    assert deleted.previous_hmac == revoked.entry_hmac
    assert [item.reason for item in ledger.load()] == ["revoked", "deleted"]
    entries, checkpoint = ledger.load_with_checkpoint()
    assert entries == (revoked, deleted)
    assert checkpoint.entry_count == 2
    assert checkpoint.head_hmac == deleted.entry_hmac
    assert ledger.latest()[PROFILE_ID].reason == "deleted"
    assert ledger.is_tombstoned(owner_user_id=7, profile_id=PROFILE_ID) is True
    assert ledger.path.stat().st_mode & 0o777 == 0o600
    assert ledger.path.parent.stat().st_mode & 0o777 == 0o700

    payload = json.loads(ledger.path.read_text(encoding="ascii").splitlines()[0])
    assert set(payload) == {
        "schema_version",
        "sequence",
        "owner_user_id",
        "profile_id",
        "deleted_at",
        "reason",
        "previous_hmac",
        "hmac",
    }
    assert not any(
        forbidden in ledger.path.read_text(encoding="ascii").lower()
        for forbidden in ("embedding", "ciphertext", "display_name", "audio")
    )


def test_tamper_truncation_extra_fields_and_unsafe_permissions_fail_closed(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.append(
        owner_user_id=7,
        profile_id=PROFILE_ID,
        deleted_at=datetime.now(timezone.utc),
        reason="deleted",
    )
    payload = json.loads(ledger.path.read_text(encoding="ascii"))
    payload["owner_user_id"] = 8
    ledger.path.write_text(json.dumps(payload) + "\n", encoding="ascii")
    ledger.path.chmod(0o600)
    with pytest.raises(VoiceprintTombstoneIntegrityError, match="authentication failed"):
        ledger.load()

    payload["unexpected"] = "not allowed"
    ledger.path.write_text(json.dumps(payload) + "\n", encoding="ascii")
    ledger.path.chmod(0o600)
    with pytest.raises(VoiceprintTombstoneIntegrityError, match="unexpected fields"):
        ledger.load()

    ledger.path.write_text("{", encoding="ascii")
    ledger.path.chmod(0o600)
    with pytest.raises(VoiceprintTombstoneIntegrityError, match="invalid JSON"):
        ledger.load()

    ledger.path.chmod(0o644)
    with pytest.raises(VoiceprintTombstoneIntegrityError, match="regular 0600"):
        ledger.load()


def test_ledger_rejects_database_backup_root_and_profile_owner_conflict(tmp_path):
    with pytest.raises(VoiceprintTombstoneConfigurationError, match="outside"):
        VoiceprintTombstoneLedger(
            path=tmp_path / "backend" / "tombstones.jsonl",
            hmac_key=b"t" * 32,
            backend_data_root=tmp_path / "backend",
        )

    ledger = _ledger(tmp_path)
    ledger.append(
        owner_user_id=7,
        profile_id=PROFILE_ID,
        deleted_at=datetime.now(timezone.utc),
        reason="revoked",
    )
    with pytest.raises(VoiceprintTombstoneIntegrityError, match="ownership changed"):
        ledger.append(
            owner_user_id=8,
            profile_id=PROFILE_ID,
            deleted_at=datetime.now(timezone.utc),
            reason="deleted",
        )


def test_from_env_requires_separate_32_byte_hmac_key(tmp_path, monkeypatch):
    path = tmp_path / "external" / "voiceprint.jsonl"
    monkeypatch.setenv("SIQ_MEETING_VOICEPRINT_TOMBSTONE_PATH", str(path))
    monkeypatch.setenv(
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_HMAC_KEY",
        base64.urlsafe_b64encode(b"h" * 32).decode("ascii"),
    )
    monkeypatch.setenv("SIQ_BACKEND_DATA_ROOT", str(tmp_path / "database-backup"))
    ledger = VoiceprintTombstoneLedger.from_env()
    assert ledger.path == path.resolve()

    monkeypatch.setenv(
        "SIQ_MEETING_VOICEPRINT_TOMBSTONE_HMAC_KEY",
        base64.urlsafe_b64encode(b"short").decode("ascii"),
    )
    with pytest.raises(VoiceprintTombstoneConfigurationError, match="32 bytes"):
        VoiceprintTombstoneLedger.from_env()


def test_append_rejects_noncanonical_identity_types(tmp_path):
    ledger = _ledger(tmp_path)
    with pytest.raises(VoiceprintTombstoneIntegrityError, match="owner is invalid"):
        ledger.append(
            owner_user_id="7",  # type: ignore[arg-type]
            profile_id=PROFILE_ID,
            deleted_at=datetime.now(timezone.utc),
            reason="deleted",
        )
    with pytest.raises(VoiceprintTombstoneIntegrityError, match="profile id is invalid"):
        ledger.append(
            owner_user_id=7,
            profile_id=7,  # type: ignore[arg-type]
            deleted_at=datetime.now(timezone.utc),
            reason="deleted",
        )
