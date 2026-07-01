"""Task lifecycle state transitions for the PDF parser queue."""

from __future__ import annotations

from datetime import timedelta

import pdf_parser_task_repository as task_repository


def stale_submitting_cutoff_iso(now, stale_seconds):
    return (now - timedelta(seconds=stale_seconds)).replace(microsecond=0).isoformat() + "Z"


def claim_next_queued_task(db_path, *, normalize_task=None, lock=None):
    return task_repository.claim_next_queued_task(
        db_path,
        normalize_task=normalize_task,
        lock=lock,
    )


def recover_stale_submitting_tasks(db_path, *, stale_seconds, now_factory, lock=None):
    cutoff = stale_submitting_cutoff_iso(now_factory(), stale_seconds)
    return task_repository.recover_stale_submitting_tasks(
        db_path,
        cutoff,
        lock=lock,
    )
