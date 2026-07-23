#!/usr/bin/env python3
"""Claim and execute durable SIQ workflow jobs outside the API process."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
from pathlib import Path
from threading import Event
from uuid import uuid4

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import create_db_and_tables  # noqa: E402
from routers import workflow  # noqa: E402
from services.workflow_queue import WorkflowLeaseLostError  # noqa: E402

logger = logging.getLogger("siq.workflow.worker")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process durable SIQ workflow jobs.")
    parser.add_argument(
        "--worker-id",
        default=os.getenv(
            "SIQ_WORKFLOW_WORKER_ID",
            f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}",
        ),
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=float(os.getenv("SIQ_WORKFLOW_WORKER_POLL_SECONDS", "1")),
    )
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def _run(arguments: argparse.Namespace) -> None:
    stop = Event()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signal_name, lambda *_: stop.set())

    while not stop.is_set():
        claim = workflow.claim_next_workflow_queue_job(arguments.worker_id)
        if claim is None:
            if arguments.once:
                return
            stop.wait(max(0.05, arguments.poll_seconds))
            continue
        job_id = str(claim.get("jobId") or "")
        try:
            workflow.process_workflow_queue_claim(claim)
        except WorkflowLeaseLostError:
            logger.warning("workflow_job_lease_lost", extra={"job_id": job_id})
        except Exception as exc:
            logger.exception("workflow_job_execution_failed", extra={"job_id": job_id})
            try:
                workflow.fail_workflow_queue_claim(claim, str(exc))
            except Exception:
                logger.exception("workflow_job_failure_publish_failed", extra={"job_id": job_id})
        if arguments.once:
            return


def main() -> None:
    arguments = _arguments()
    logging.basicConfig(
        level=os.getenv("SIQ_WORKFLOW_WORKER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if workflow.WORKFLOW_JOB_BACKEND != "postgres":
        logger.info("workflow_worker_disabled", extra={"backend": workflow.WORKFLOW_JOB_BACKEND})
        return
    create_db_and_tables()
    _run(arguments)


if __name__ == "__main__":
    main()
