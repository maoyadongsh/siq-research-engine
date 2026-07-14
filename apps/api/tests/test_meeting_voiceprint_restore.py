from __future__ import annotations

from datetime import datetime, timezone

import anyio
from scripts.reconcile_meeting_voiceprint_tombstones import reconcile_and_verify
from services.auth_service import User
from services.meeting_contracts import (
    MEETING_TABLES,
    MeetingVoiceprintConsent,
    MeetingVoiceProfile,
    VoiceProfileStatus,
)
from services.meeting_voiceprint_tombstone import (
    EMPTY_TOMBSTONE_HEAD_HMAC,
    VoiceprintTombstoneConfigurationError,
    VoiceprintTombstoneIntegrityError,
    VoiceprintTombstoneLedger,
)
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession


def test_restore_replay_purges_template_and_every_active_consent(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'restored.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=[User.__table__, *[model.__table__ for model in MEETING_TABLES]],
                )
            )
        profile = MeetingVoiceProfile(
            owner_user_id=7,
            display_name="Restored Profile",
            status=VoiceProfileStatus.ACTIVE.value,
            encoder_name="funasr-eres2netv2",
            encoder_version="encoder-v1",
            encrypted_embedding="restored-ciphertext",
            key_id="restored-key",
        )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(profile)
            session.add(
                MeetingVoiceprintConsent(
                    voice_profile_id=profile.id,
                    actor_user_id=7,
                    subject_label="Restored Profile",
                    policy_version="voiceprint-consent.v1",
                    source_meeting_id="11111111-1111-4111-8111-111111111111",
                )
            )
            session.add(
                MeetingVoiceprintConsent(
                    voice_profile_id=profile.id,
                    actor_user_id=8,
                    subject_label="Inconsistent restored consent",
                    policy_version="voiceprint-consent.v1",
                    source_meeting_id="11111111-1111-4111-8111-111111111111",
                )
            )
            await session.commit()

        ledger = VoiceprintTombstoneLedger(
            path=tmp_path / "external-security" / "voiceprint-tombstones.jsonl",
            hmac_key=b"h" * 32,
            backend_data_root=tmp_path / "database-backup",
        )
        ledger.append(
            owner_user_id=7,
            profile_id=profile.id,
            deleted_at=datetime.now(timezone.utc),
            reason="deleted",
        )

        before = await reconcile_and_verify(engine=engine, ledger=ledger, apply=False)
        assert before["status"] == "failed"
        assert before["residual_profile_count"] == 1
        assert before["active_consent_count"] == 2

        _, checkpoint = ledger.load_with_checkpoint()
        after = await reconcile_and_verify(
            engine=engine,
            ledger=ledger,
            apply=True,
            require_ledger_checkpoint=True,
            expected_ledger_count=checkpoint.entry_count,
            expected_ledger_head_hmac=checkpoint.head_hmac,
        )
        assert after["status"] == "passed"
        assert after["ledger_entry_count"] == 1
        assert after["ledger_head_hmac"] == checkpoint.head_hmac
        assert after["ledger_checkpoint_verified"] is True
        assert after["selected_tombstone_count"] == 1
        assert after["matched_profile_count"] == 1
        assert after["active_consent_count"] == 0
        async with AsyncSession(engine, expire_on_commit=False) as session:
            restored = (await session.exec(select(MeetingVoiceProfile))).one()
            consents = list((await session.exec(select(MeetingVoiceprintConsent))).all())
        assert restored.status == VoiceProfileStatus.DELETED.value
        assert restored.encrypted_embedding is None
        assert restored.key_id is None
        assert all(consent.revoked_at is not None for consent in consents)
        await engine.dispose()

    anyio.run(scenario)


def test_required_restore_rejects_old_valid_prefix_before_replay(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'prefix.db'}")
        ledger = VoiceprintTombstoneLedger(
            path=tmp_path / "external-security" / "voiceprint-tombstones.jsonl",
            hmac_key=b"h" * 32,
            backend_data_root=tmp_path / "database-backup",
        )
        first = ledger.append(
            owner_user_id=7,
            profile_id="11111111-1111-4111-8111-111111111111",
            deleted_at=datetime.now(timezone.utc),
            reason="revoked",
        )
        ledger.append(
            owner_user_id=7,
            profile_id="22222222-2222-4222-8222-222222222222",
            deleted_at=datetime.now(timezone.utc),
            reason="deleted",
        )

        try:
            await reconcile_and_verify(
                engine=engine,
                ledger=ledger,
                apply=True,
                require_ledger_checkpoint=True,
                expected_ledger_count=1,
                expected_ledger_head_hmac=first.entry_hmac,
            )
        except VoiceprintTombstoneIntegrityError as exc:
            assert "external checkpoint" in str(exc)
        else:
            raise AssertionError("a valid but stale ledger prefix must fail before replay")
        await engine.dispose()

    anyio.run(scenario)


def test_required_restore_accepts_empty_checkpoint_from_environment(tmp_path, monkeypatch):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'empty-checkpoint.db'}")
        ledger = VoiceprintTombstoneLedger(
            path=tmp_path / "external-security" / "voiceprint-tombstones.jsonl",
            hmac_key=b"h" * 32,
            backend_data_root=tmp_path / "database-backup",
        )
        ledger.initialize()
        monkeypatch.setenv("SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_COUNT", "0")
        monkeypatch.setenv(
            "SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC",
            EMPTY_TOMBSTONE_HEAD_HMAC,
        )

        report = await reconcile_and_verify(
            engine=engine,
            ledger=ledger,
            apply=True,
            require_ledger_file=True,
            require_ledger_checkpoint=True,
        )

        assert report["status"] == "passed"
        assert report["ledger_entry_count"] == 0
        assert report["ledger_head_hmac"] == EMPTY_TOMBSTONE_HEAD_HMAC
        assert report["ledger_checkpoint_verified"] is True
        await engine.dispose()

    anyio.run(scenario)


def test_required_restore_rejects_an_uninitialized_external_ledger(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'empty.db'}")
        ledger = VoiceprintTombstoneLedger(
            path=tmp_path / "external-security" / "missing.jsonl",
            hmac_key=b"h" * 32,
            backend_data_root=tmp_path / "database-backup",
        )
        try:
            await reconcile_and_verify(
                engine=engine,
                ledger=ledger,
                apply=True,
                require_ledger_file=True,
            )
        except VoiceprintTombstoneConfigurationError:
            pass
        else:
            raise AssertionError("an uninitialized required ledger must fail closed")
        await engine.dispose()

    anyio.run(scenario)
