#!/usr/bin/env python3
"""Verify or replay external meeting-deletion tombstones after a restore."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Sequence

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import async_engine  # noqa: E402
from services.meeting_retention import (  # noqa: E402
    MeetingDeletionLedger,
    MeetingDeletionLedgerError,
    MeetingRetentionWorker,
    MeetingStoragePurger,
)
from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: E402
from sqlmodel.ext.asyncio.session import AsyncSession  # noqa: E402


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Purge restored content and files before running the verification probe.",
    )
    parser.add_argument(
        "--require-ledger-file",
        action="store_true",
        help="Fail closed when the external ledger has not been restored/mounted.",
    )
    return parser.parse_args(argv)


async def reconcile_and_verify(*, apply: bool, require_ledger_file: bool) -> dict[str, object]:
    ledger = MeetingDeletionLedger.from_env()
    if require_ledger_file and (not ledger.path.is_file() or ledger.path.is_symlink()):
        raise MeetingDeletionLedgerError(
            "DELETE_LEDGER_REQUIRED",
            "meeting deletion ledger must be mounted before restore acceptance",
            retryable=False,
        )
    factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    worker = MeetingRetentionWorker(
        factory,
        ledger=ledger,
        purger=MeetingStoragePurger(),
        worker_id="meeting-deletion-reconcile",
    )
    report = await worker.reconcile_tombstones(apply=apply)
    return {
        **report.as_dict(),
        "mode": "apply-and-verify" if apply else "verify-only",
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        report = asyncio.run(
            reconcile_and_verify(
                apply=arguments.apply,
                require_ledger_file=arguments.require_ledger_file,
            )
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "schema_version": "siq.meeting.deletion_reconcile.v1",
                    "status": "failed",
                    "error_code": getattr(exc, "code", type(exc).__name__),
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
