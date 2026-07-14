#!/usr/bin/env python3
"""Replay and verify external meeting voiceprint deletion tombstones."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence, TypeVar

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import async_engine  # noqa: E402
from services.meeting_contracts import (  # noqa: E402
    MeetingVoiceprintConsent,
    MeetingVoiceProfile,
    VoiceProfileStatus,
)
from services.meeting_repository import MeetingRepository  # noqa: E402
from services.meeting_voiceprint_tombstone import (  # noqa: E402
    EMPTY_TOMBSTONE_HEAD_HMAC,
    VoiceprintTombstone,
    VoiceprintTombstoneCheckpoint,
    VoiceprintTombstoneConfigurationError,
    VoiceprintTombstoneIntegrityError,
    VoiceprintTombstoneLedger,
)
from sqlalchemy.ext.asyncio import AsyncEngine  # noqa: E402
from sqlmodel import col, select  # noqa: E402
from sqlmodel.ext.asyncio.session import AsyncSession  # noqa: E402

_T = TypeVar("_T")
_BATCH_SIZE = 500
_EXPECTED_COUNT_ENV = "SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_COUNT"
_EXPECTED_HEAD_ENV = "SIQ_MEETING_VOICEPRINT_TOMBSTONE_EXPECTED_HEAD_HMAC"


def _batches(values: Sequence[_T], size: int = _BATCH_SIZE) -> Iterable[Sequence[_T]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def _expected_checkpoint(
    *,
    expected_count: int | None,
    expected_head_hmac: str | None,
    required: bool,
) -> VoiceprintTombstoneCheckpoint | None:
    configured = expected_count is not None or expected_head_hmac is not None
    if not configured and required:
        raw_count = os.getenv(_EXPECTED_COUNT_ENV, "").strip()
        raw_head = os.getenv(_EXPECTED_HEAD_ENV, "").strip()
        if not raw_count or not raw_head:
            raise VoiceprintTombstoneConfigurationError(
                "voiceprint tombstone checkpoint is required"
            )
        if not re.fullmatch(r"0|[1-9][0-9]*", raw_count):
            raise VoiceprintTombstoneConfigurationError(
                "voiceprint tombstone expected count is invalid"
            )
        expected_count = int(raw_count)
        expected_head_hmac = raw_head
        configured = True
    if not configured:
        return None
    if type(expected_count) is not int or expected_count < 0:
        raise VoiceprintTombstoneConfigurationError(
            "voiceprint tombstone expected count is invalid"
        )
    if not isinstance(expected_head_hmac, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", expected_head_hmac.strip()
    ):
        raise VoiceprintTombstoneConfigurationError(
            "voiceprint tombstone expected head HMAC is invalid"
        )
    normalized_head = expected_head_hmac.strip().lower()
    if expected_count == 0 and normalized_head != EMPTY_TOMBSTONE_HEAD_HMAC:
        raise VoiceprintTombstoneConfigurationError(
            "an empty voiceprint tombstone checkpoint must use the zero head HMAC"
        )
    return VoiceprintTombstoneCheckpoint(
        entry_count=expected_count,
        head_hmac=normalized_head,
    )


def _assert_checkpoint(
    actual: VoiceprintTombstoneCheckpoint,
    expected: VoiceprintTombstoneCheckpoint | None,
) -> None:
    if expected is not None and actual != expected:
        raise VoiceprintTombstoneIntegrityError(
            "voiceprint tombstone ledger does not match the external checkpoint"
        )


async def _verify(
    engine: AsyncEngine,
    entries: Sequence[VoiceprintTombstone],
) -> dict[str, int]:
    if not entries:
        return {
            "matched_profile_count": 0,
            "residual_profile_count": 0,
            "active_consent_count": 0,
            "ownership_mismatch_count": 0,
        }
    expected = {entry.profile_id: entry for entry in entries}
    profiles: list[MeetingVoiceProfile] = []
    async with AsyncSession(engine, expire_on_commit=False) as session:
        profile_ids = sorted(expected)
        for batch in _batches(profile_ids):
            profiles.extend(
                list(
                    (
                        await session.exec(
                            select(MeetingVoiceProfile).where(
                                col(MeetingVoiceProfile.id).in_(batch)
                            )
                        )
                    ).all()
                )
            )
        matched_ids = [profile.id for profile in profiles]
        active_consent_count = 0
        for batch in _batches(matched_ids):
            active_consent_count += len(
                (
                    await session.exec(
                        select(MeetingVoiceprintConsent.id).where(
                            col(MeetingVoiceprintConsent.voice_profile_id).in_(batch),
                            MeetingVoiceprintConsent.revoked_at.is_(None),
                        )
                    )
                ).all()
            )

    residual_profile_count = 0
    ownership_mismatch_count = 0
    for profile in profiles:
        tombstone = expected[profile.id]
        if profile.owner_user_id != tombstone.owner_user_id:
            ownership_mismatch_count += 1
            continue
        allowed_statuses = (
            {VoiceProfileStatus.DELETED.value}
            if tombstone.reason == "deleted"
            else {VoiceProfileStatus.REVOKED.value, VoiceProfileStatus.DELETED.value}
        )
        if (
            profile.encrypted_embedding is not None
            or profile.key_id is not None
            or profile.status not in allowed_statuses
        ):
            residual_profile_count += 1
    return {
        "matched_profile_count": len(profiles),
        "residual_profile_count": residual_profile_count,
        "active_consent_count": active_consent_count,
        "ownership_mismatch_count": ownership_mismatch_count,
    }


async def reconcile_and_verify(
    *,
    engine: AsyncEngine = async_engine,
    ledger: VoiceprintTombstoneLedger | None = None,
    apply: bool,
    owner_user_id: int | None = None,
    require_ledger_file: bool = False,
    require_ledger_checkpoint: bool = False,
    expected_ledger_count: int | None = None,
    expected_ledger_head_hmac: str | None = None,
) -> dict[str, Any]:
    tombstones = ledger or VoiceprintTombstoneLedger.from_env()
    if require_ledger_file and not tombstones.path.is_file():
        raise VoiceprintTombstoneConfigurationError(
            "voiceprint tombstone ledger must be initialized before restore acceptance"
        )
    expected_checkpoint = _expected_checkpoint(
        expected_count=expected_ledger_count,
        expected_head_hmac=expected_ledger_head_hmac,
        required=require_ledger_checkpoint,
    )
    complete_entries, checkpoint = tombstones.load_with_checkpoint()
    _assert_checkpoint(checkpoint, expected_checkpoint)
    latest: dict[str, VoiceprintTombstone] = {}
    for entry in complete_entries:
        latest[entry.profile_id] = entry
    entries = sorted(
        (
            entry
            for entry in latest.values()
            if owner_user_id is None or entry.owner_user_id == owner_user_id
        ),
        key=lambda entry: (entry.owner_user_id, entry.profile_id),
    )
    reconcile_result = {"seen": len(entries), "purged": 0, "remaining": 0}
    if apply:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            reconcile_result = await MeetingRepository(
                session,
                voiceprint_tombstones=tombstones,
            ).reconcile_voiceprint_tombstones(owner_user_id=owner_user_id)
        _, checkpoint_after_replay = tombstones.load_with_checkpoint()
        _assert_checkpoint(checkpoint_after_replay, expected_checkpoint)
        checkpoint = checkpoint_after_replay
    verification = await _verify(engine, entries)
    passed = (
        reconcile_result["remaining"] == 0
        and verification["residual_profile_count"] == 0
        and verification["active_consent_count"] == 0
        and verification["ownership_mismatch_count"] == 0
    )
    return {
        "schema_version": "siq.meeting.voiceprint_tombstone_reconcile.v1",
        "status": "passed" if passed else "failed",
        "mode": "apply-and-verify" if apply else "verify-only",
        "ledger_entry_count": checkpoint.entry_count,
        "ledger_head_hmac": checkpoint.head_hmac,
        "ledger_checkpoint_verified": expected_checkpoint is not None,
        "selected_tombstone_count": len(entries),
        "purged_profile_count": reconcile_result["purged"],
        **verification,
    }


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Purge restored templates before running the verification probe.",
    )
    parser.add_argument("--owner-user-id", type=int)
    parser.add_argument(
        "--require-ledger-file",
        action="store_true",
        help="Fail when the external ledger has not been initialized.",
    )
    parser.add_argument(
        "--require-ledger-checkpoint",
        action="store_true",
        help="Require the external expected count and head HMAC before replay.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    if arguments.owner_user_id is not None and arguments.owner_user_id <= 0:
        raise SystemExit("--owner-user-id must be positive")
    try:
        report = asyncio.run(
            reconcile_and_verify(
                apply=arguments.apply,
                owner_user_id=arguments.owner_user_id,
                require_ledger_file=arguments.require_ledger_file,
                require_ledger_checkpoint=arguments.require_ledger_checkpoint,
            )
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "schema_version": "siq.meeting.voiceprint_tombstone_reconcile.v1",
                    "status": "failed",
                    "error_code": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
